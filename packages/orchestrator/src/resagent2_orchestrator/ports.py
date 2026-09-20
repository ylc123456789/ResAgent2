"""Child-module boundary and deterministic fake implementation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Protocol

from resagent2_contracts import (
    AgentOwner,
    AgentResult,
    AgentRequest,
)


class ModulePort(Protocol):
    """Uniform boundary implemented by native Agents or other task adapters."""

    def invoke(self, request: AgentRequest) -> AgentResult:
        """Execute until a result or pause; resume may invoke the same Attempt again."""


@dataclass(frozen=True, slots=True)
class ModuleBinding:
    """Execution Agent owner and invocation port.

    The workspace is no longer fixed here: the Scheduler resolves each task's
    ``workspace_id`` against ``ResearchRun.workspaces`` and derives a
    per-attempt ``WorkspaceGrant``.
    """

    owner: AgentOwner
    port: ModulePort


class ScriptedModulePort:
    """Deterministic fake ModulePort that returns predefined results."""

    def __init__(self, results: list[AgentResult]) -> None:
        self._results = deque(results)
        self.requests: list[AgentRequest] = []

    def invoke(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        if not self._results:
            raise RuntimeError("scripted ModulePort has no remaining result")
        return self._results.popleft()
