"""Session persistence protocol and in-memory implementation."""

from __future__ import annotations

from typing import Protocol

from resagent2_contracts import SessionId

from .models import AgentState


class SessionStore(Protocol):
    """Persistence boundary used by AgentLoop after every state change."""

    def save(self, state: AgentState) -> None:
        """Persist a complete session snapshot."""

    def load(self, session_id: SessionId) -> AgentState:
        """Load the latest session snapshot."""

    def exists(self, session_id: SessionId) -> bool:
        """Return whether a session already exists."""


class InMemorySessionStore:
    """Deep-copying session store for tests and local runtime development."""

    def __init__(self) -> None:
        self._current: dict[str, AgentState] = {}
        self._history: dict[str, list[AgentState]] = {}

    def save(self, state: AgentState) -> None:
        snapshot = state.model_copy(deep=True)
        self._current[state.session_id] = snapshot
        self._history.setdefault(state.session_id, []).append(snapshot)

    def load(self, session_id: SessionId) -> AgentState:
        return self._current[session_id].model_copy(deep=True)

    def exists(self, session_id: SessionId) -> bool:
        return session_id in self._current

    def history(self, session_id: SessionId) -> list[AgentState]:
        """Return independent snapshots for persistence assertions and debugging."""

        return [state.model_copy(deep=True) for state in self._history[session_id]]
