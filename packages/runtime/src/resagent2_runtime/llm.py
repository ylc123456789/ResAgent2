"""LLM client protocol and deterministic scripted test client."""

from __future__ import annotations

from collections import deque
from typing import Protocol

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
