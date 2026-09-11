"""LLM client protocol and deterministic scripted test client."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel

from .context import ContextBudgetExceeded, ContextComposer
from .models import ComposedContext, ContextSection


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """Configured context limits for one injected model client."""

    context_window: int
    reserved_output_tokens: int = 4096
    safety_margin_tokens: int = 1024

    def __post_init__(self) -> None:
        if self.context_window < 1 or self.reserved_output_tokens < 1:
            raise ValueError("context window and output reservation must be positive")
        if self.safety_margin_tokens < 0:
            raise ValueError("safety margin cannot be negative")
        if (
            self.reserved_output_tokens + self.safety_margin_tokens
            >= self.context_window
        ):
            raise ValueError("model profile leaves no room for input context")

    def input_budget(
        self,
        *,
        schema_tokens: int,
        component_limit: int,
    ) -> int:
        """Return the smaller of the model capacity and component policy."""

        if schema_tokens < 0:
            raise ValueError("schema_tokens cannot be negative")
        if component_limit < 1:
            raise ValueError("component_limit must be positive")
        available = (
            self.context_window
            - self.reserved_output_tokens
            - self.safety_margin_tokens
            - schema_tokens
        )
        if available < 1:
            raise ValueError("model profile leaves no room after action schema")
        return min(component_limit, available)


class LLMClient(Protocol):
    """Provider-neutral interface: only ``next_action`` is required.

    Consumers feature-detect optional budget and trace hooks. A minimal client
    needs none of them; absent attempt accounting means one call per request.
    """

    def next_action(
        self,
        context: ComposedContext,
        action_type: type[BaseModel],
    ) -> BaseModel | dict:
        """Return a candidate, or raise JSONDecodeError for malformed output.

        The caller owns schema validation and bounded correction feedback;
        transport failures remain separate from invalid model output.
        """


class PromptLLMClient:
    """Adapt plain prompts to budgeted context without running an AgentLoop.

    The caller supplies its own system prompt and section label. Optional
    provider hooks retain the same semantics as direct runtime-client use.
    """

    def __init__(
        self,
        client: LLMClient,
        *,
        system_prompt: str,
        max_context_tokens: int,
        section_name: str = "request",
    ) -> None:
        if max_context_tokens < 1:
            raise ValueError("max_context_tokens must be positive")
        self._client = client
        self._system_prompt = system_prompt
        self._max_context_tokens = max_context_tokens
        self._section_name = section_name
        self._composer = ContextComposer()
        self.last_attempts = 0

    def set_trace_context(self, **kwargs) -> None:
        tracer = getattr(self._client, "set_trace_context", None)
        if tracer is not None:
            tracer(**kwargs)

    def set_attempt_limit(self, max_attempts: int) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        limiter = getattr(self._client, "set_attempt_limit", None)
        if limiter is not None:
            limiter(max_attempts)

    def next_action(
        self, prompt: str, action_type: type[BaseModel],
    ) -> BaseModel | dict:
        # A budget failure makes no provider call, even after a previous retry.
        self.last_attempts = 0
        budgeter = getattr(self._client, "context_budget", None)
        max_tokens = (
            budgeter(action_type, self._max_context_tokens)
            if budgeter is not None else self._max_context_tokens
        )
        context = self._composer.compose(
            self._system_prompt,
            [ContextSection(
                name=self._section_name, content=prompt, required=True,
            )],
            max_tokens=max_tokens,
        )
        try:
            return self._client.next_action(context, action_type)
        finally:
            self.last_attempts = getattr(self._client, "last_attempts", 1)


class LLMExhaustedError(RuntimeError):
    """Raised when a scripted client has no action left to return."""


class ScriptedLLMClient:
    """Deterministic mock LLM that returns predefined structured candidates."""

    def __init__(self, actions: list[BaseModel | dict]) -> None:
        self._actions = deque(actions)
        self.contexts: list[ComposedContext] = []

    def next_action(
        self,
        context: ComposedContext,
        action_type: type[BaseModel],
    ) -> BaseModel | dict:
        """Record context and return the next scripted candidate."""

        self.contexts.append(context)
        if not self._actions:
            raise LLMExhaustedError("scripted LLM has no remaining action")
        return self._actions.popleft()


class OpenAICompatibleClient:
    """Minimal stateless client for one JSON response per chat-completions call."""

    def __init__(
        self,
        *,
        model: str,
        api_base: str,
        api_key_env: str,
        model_profile: ModelProfile | None = None,
        timeout_seconds: int = 120,
        trace_dir: Path | None = None,
        trace_level: str = "off",
    ) -> None:
        self.model = model
        self.endpoint = f"{api_base.rstrip('/')}/chat/completions"
        self.api_key_env = api_key_env
        self.model_profile = model_profile
        self.timeout_seconds = timeout_seconds
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.trace_level = trace_level
        self._trace_context: dict = {}
        self._trace_seq = 0
        self._last_call_id: str | None = None
        self.last_attempts = 0
        self._attempt_limit: int | None = None

    @staticmethod
    def _action_instruction(action_type: type[BaseModel]) -> str:
        schema = json.dumps(action_type.model_json_schema(), ensure_ascii=False)
        return (
            "Return exactly one JSON object matching this action schema. "
            "Do not use markdown fences.\n"
            f"{schema}"
        )

    def context_budget(
        self,
        action_type: type[BaseModel],
        component_limit: int,
    ) -> int:
        """Return this component's usable input budget for one action schema."""

        if self.model_profile is None:
            return component_limit
        schema_tokens = ContextComposer.estimate_tokens(
            self._action_instruction(action_type)
        )
        try:
            return self.model_profile.input_budget(
                schema_tokens=schema_tokens,
                component_limit=component_limit,
            )
        except ValueError as error:
            raise ContextBudgetExceeded(str(error)) from error

    def set_attempt_limit(self, max_attempts: int) -> None:
        """Limit provider attempts for the next call only."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._attempt_limit = max_attempts

    def set_trace_context(self, **kwargs) -> None:
        """Attach per-call correlation fields for the optional JSONL trace."""
        self._trace_context = dict(kwargs)

    def _write_trace(self, record: dict) -> None:
        """Append one JSONL trace line (the API key is never recorded)."""
        if self.trace_dir is None or self.trace_level == "off":
            return
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.trace_dir, 0o700)
        self._trace_seq += 1
        line = json.dumps(
            {"sequence": self._trace_seq, **record}, ensure_ascii=False, default=str
        )
        path = self.trace_dir / "llm_traces.jsonl"
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        os.chmod(path, 0o600)

    def record_validation(self, validation_error: str) -> None:
        """Record an action-schema validation failure, keyed by the last call_id."""
        if self.trace_dir is None or self.trace_level == "off":
            return
        self._write_trace(
            {
                "call_id": self._last_call_id,
                "schema_validation_error": validation_error,
            }
        )

    @staticmethod
    def _sha256(text: str | None) -> str | None:
        if text is None:
            return None
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False, default=str)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _response_trace_fields(
        self,
        parsed_action,
        raw_response_text,
        raw_reasoning_text: str | None,
        validation_error: str | None,
        usage,
        finish_reason: str | None,
    ) -> dict:
        """Project one response with the same content boundary at every attempt."""
        tool = parsed_action.get("tool") if isinstance(parsed_action, dict) else None
        record = {
            "tool": tool,
            "action_valid": parsed_action is not None,
            "validation_error": validation_error,
            "usage": usage,
            "finish_reason": finish_reason,
        }
        if self.trace_level == "full":
            record["raw_response_text"] = raw_response_text
            record["parsed_action"] = parsed_action
            record["raw_reasoning_text"] = raw_reasoning_text
        else:
            record["response_sha256"] = self._sha256(raw_response_text)
            record["action_sha256"] = (
                self._sha256(json.dumps(parsed_action, ensure_ascii=False, default=str))
                if parsed_action is not None
                else None
            )
        return record

    def _trace_record(
        self,
        context: ComposedContext,
        message: str,
        started: float,
        call_id: str,
        created_at: str,
        request_max_tokens: int | None,
        attempts: list[dict],
    ) -> dict:
        """Keep one logical-call row; response fields describe its last attempt.

        Bounded attempt details retain earlier failures without multiplying
        logical-call rows or changing retry accounting. A null output limit
        means the request omitted max_tokens, not that the provider is unlimited.
        """
        record = {**self._trace_context, **attempts[-1]}
        record.update({
            "call_id": call_id,
            "created_at": created_at,
            "model": self.model,
            "included_sections": context.included_sections,
            "omitted_sections": context.omitted_sections,
            "estimated_tokens": context.estimated_tokens,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "request_max_tokens": request_max_tokens,
            "attempts": attempts,
        })
        if self.trace_level == "full":
            record["request_text"] = message
        else:
            record["request_sha256"] = self._sha256(message)
        return record

    def next_action(
        self,
        context: ComposedContext,
        action_type: type[BaseModel],
    ) -> BaseModel | dict:
        attempt_limit = min(3, self._attempt_limit or 3)
        self._attempt_limit = None
        self.last_attempts = 0
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"missing API key environment variable {self.api_key_env}")
        message = f"{context.text}\n\n{self._action_instruction(action_type)}"
        body_data = {
            "model": self.model,
            "messages": [{"role": "user", "content": message}],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        if self.model_profile is not None:
            body_data["max_tokens"] = self.model_profile.reserved_output_tokens
        body = json.dumps(body_data).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        call_id = uuid.uuid4().hex
        created_at = datetime.now(UTC).isoformat()
        self._last_call_id = call_id
        started = time.monotonic()
        last_error: Exception | None = None
        parsed_action = None
        attempts: list[dict] = []
        try:
            for attempt in range(attempt_limit):
                if attempt:
                    time.sleep(1.0)
                self.last_attempts = attempt + 1
                # Never attribute an earlier response to a later transport failure.
                raw_response_text = None
                raw_reasoning_text = None
                parsed_action = None
                usage = None
                finish_reason = None
                last_error = None
                try:
                    with urlopen(request, timeout=self.timeout_seconds) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise TypeError("provider response must be an object")
                    # Capture provider diagnostics before parsing the action JSON.
                    usage = payload.get("usage")
                    choice = payload["choices"][0]
                    if not isinstance(choice, dict):
                        raise TypeError("provider choice must be an object")
                    finish_reason = choice.get("finish_reason")
                    response_message = choice["message"]
                    if not isinstance(response_message, dict):
                        raise TypeError("provider message must be an object")
                    reasoning_content = response_message.get("reasoning_content")
                    raw_reasoning_text = (
                        reasoning_content if isinstance(reasoning_content, str) else None
                    )
                    raw_response_text = response_message["content"]
                    if not isinstance(raw_response_text, str):
                        raise TypeError(
                            "provider returned non-string message content: "
                            f"{type(raw_response_text).__name__}"
                        )
                    content = raw_response_text.strip()
                    if content.startswith("```"):
                        content = content.removeprefix("```json").removeprefix("```")
                        content = content.removesuffix("```").strip()
                except HTTPError as error:
                    detail = error.read(2000).decode("utf-8", errors="replace")
                    last_error = error
                    # Preserve the existing no-retry policy for client errors.
                    if error.code is not None and error.code < 500:
                        last_error = RuntimeError(f"LLM HTTP {error.code}: {detail}")
                        raise last_error from error
                except (
                    URLError, TimeoutError, json.JSONDecodeError,
                    KeyError, IndexError, TypeError,
                ) as error:
                    last_error = error
                else:
                    # The provider envelope was valid. Malformed model output
                    # needs caller feedback, not an identical HTTP retry. Parse
                    # outside the transport handlers so the error reaches the
                    # caller, while both finally blocks still preserve trace.
                    try:
                        parsed_action = json.loads(content)
                    except json.JSONDecodeError as error:
                        last_error = error
                        raise
                    break
                finally:
                    attempts.append({
                        "retry_number": attempt,
                        **self._response_trace_fields(
                            parsed_action, raw_response_text, raw_reasoning_text,
                            str(last_error) if last_error is not None else None,
                            usage, finish_reason,
                        ),
                    })
        finally:
            if attempts:
                self._write_trace(self._trace_record(
                    context, message, started, call_id, created_at,
                    body_data.get("max_tokens"), attempts,
                ))
        if last_error is not None:
            raise RuntimeError(
                f"LLM request failed after {self.last_attempts} attempts: {last_error}"
            ) from last_error
        return parsed_action
