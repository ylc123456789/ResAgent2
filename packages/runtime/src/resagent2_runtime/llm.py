"""LLM client protocol and deterministic scripted test client."""

from __future__ import annotations

from collections import deque
import json
import os
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
    ) -> None:
        self.model = model
        self.endpoint = f"{api_base.rstrip('/')}/chat/completions"
        self.api_key_env = api_key_env
        self.timeout_seconds = timeout_seconds

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
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read(2000).decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM HTTP {error.code}: {detail}") from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RuntimeError(f"LLM request failed: {error}") from error
        try:
            content = payload["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.removeprefix("```json").removeprefix("```")
                content = content.removesuffix("```").strip()
            return json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("LLM response did not contain one JSON action") from error
