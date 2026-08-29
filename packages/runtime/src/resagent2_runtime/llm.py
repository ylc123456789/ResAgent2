"""LLM client protocol and deterministic scripted test client."""

from __future__ import annotations

from collections import deque
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import AgentAction, ComposedContext


class LLMClient(Protocol):
    """Provider-neutral interface for requesting one structured Agent action."""

    def next_action(
        self,
        context: ComposedContext,
        action_type: type[AgentAction],
    ) -> AgentAction | dict:
        """Return one action candidate for schema validation by AgentLoop."""


class LLMExhaustedError(RuntimeError):
    """Raised when a scripted client has no action left to return."""


class ScriptedLLMClient:
    """Deterministic mock LLM that returns a predefined action sequence."""

    def __init__(self, actions: list[AgentAction | dict]) -> None:
        self._actions = deque(actions)
        self.contexts: list[ComposedContext] = []

    def next_action(
        self,
        context: ComposedContext,
        action_type: type[AgentAction],
    ) -> AgentAction | dict:
        """Record context and return the next scripted action."""

        self.contexts.append(context)
        if not self._actions:
            raise LLMExhaustedError("scripted LLM has no remaining action")
        return self._actions.popleft()


class OpenAICompatibleClient:
    """Minimal stateless client for one JSON action per chat-completions call."""

    def __init__(
        self,
        *,
        model: str,
        api_base: str,
        api_key_env: str,
        timeout_seconds: int = 120,
        trace_dir: Path | None = None,
        trace_level: str = "off",
    ) -> None:
        self.model = model
        self.endpoint = f"{api_base.rstrip('/')}/chat/completions"
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.trace_level = trace_level
        self._trace_context: dict = {}
        self._trace_seq = 0

    def set_trace_context(self, **kwargs) -> None:
        """Attach per-call correlation fields for the optional JSONL trace."""
        self._trace_context = dict(kwargs)

    def _write_trace(self, record: dict) -> None:
        """Append one JSONL trace line (the API key is never recorded)."""
        if self.trace_dir is None or self.trace_level == "off":
            return
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self._trace_seq += 1
        line = json.dumps(
            {"sequence": self._trace_seq, **record}, ensure_ascii=False, default=str
        )
        with (self.trace_dir / "llm_traces.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    @staticmethod
    def _sha256(text: str | None) -> str | None:
        if text is None:
            return None
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _trace_record(
        self,
        context: ComposedContext,
        message: str,
        parsed_action,
        raw_response_text: str | None,
        validation_error: str | None,
        started: float,
        retry_number: int,
        usage,
    ) -> dict:
        """Build one trace record; the full level keeps request/response text."""
        record = dict(self._trace_context)
        record.update(
            {
                "model": self.model,
                "included_sections": context.included_sections,
                "omitted_sections": context.omitted_sections,
                "estimated_tokens": context.estimated_tokens,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "retry_number": retry_number,
                "parsed_action": parsed_action,
                "validation_error": validation_error,
                "usage": usage,
            }
        )
        if self.trace_level == "full":
            record["request_text"] = message
            record["raw_response_text"] = raw_response_text
        else:
            record["request_sha256"] = self._sha256(message)
            record["response_sha256"] = self._sha256(raw_response_text)
        return record

    def next_action(
        self,
        context: ComposedContext,
        action_type: type[AgentAction],
    ) -> AgentAction | dict:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"missing API key environment variable {self.api_key_env}")
        schema = json.dumps(action_type.model_json_schema(), ensure_ascii=False)
        message = (
            f"{context.text}\n\n"
            "Return exactly one JSON object matching this action schema. "
            "Do not use markdown fences.\n"
            f"{schema}"
        )
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": message}],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            }
        ).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.monotonic()
        last_error: Exception | None = None
        retry_number = 0
        raw_response_text: str | None = None
        parsed_action = None
        usage = None
        for attempt in range(3):
            if attempt:
                time.sleep(1.0)
                retry_number = attempt
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                detail = error.read(2000).decode("utf-8", errors="replace")
                # A 4xx is a client error (auth/schema), not a transient one.
                if error.code is not None and error.code < 500:
                    self._write_trace(
                        self._trace_record(
                            context, message, None, None,
                            f"LLM HTTP {error.code}: {detail}",
                            started, retry_number, None,
                        )
                    )
                    raise RuntimeError(f"LLM HTTP {error.code}: {detail}") from error
                last_error = error
                continue
            except (URLError, TimeoutError, json.JSONDecodeError) as error:
                last_error = error
                continue
            try:
                content = payload["choices"][0]["message"]["content"].strip()
                if content.startswith("```"):
                    content = content.removeprefix("```json").removeprefix("```")
                    content = content.removesuffix("```").strip()
                parsed_action = json.loads(content)
                raw_response_text = content
                usage = payload.get("usage")
                last_error = None
                break
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
                last_error = error
                continue
        self._write_trace(
            self._trace_record(
                context, message, parsed_action, raw_response_text,
                str(last_error) if last_error is not None else None,
                started, retry_number, usage,
            )
        )
        if last_error is not None:
            raise RuntimeError(
                f"LLM request failed after 3 attempts: {last_error}"
            ) from last_error
        return parsed_action
