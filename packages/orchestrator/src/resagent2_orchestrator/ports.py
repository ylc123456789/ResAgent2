"""Child-module boundary and deterministic fake implementation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Protocol

from resagent2_contracts import (
    AgentOwner,
    ModuleResult,
    ModuleTaskRequest,
)


class ModulePort(Protocol):
    """Uniform boundary implemented by native Agents or legacy adapters."""

    def invoke(self, request: ModuleTaskRequest) -> ModuleResult:
        """Execute one complete task attempt and return a structured result."""


@dataclass(frozen=True, slots=True)
class ModuleBinding:
    """Capability owner and invocation port.

    The workspace is no longer fixed here: the Scheduler resolves each task's
    ``workspace_id`` against ``ResearchRun.workspaces`` and derives a
    per-attempt ``WorkspaceGrant``.
    """

    owner: AgentOwner
    port: ModulePort


class ScriptedModulePort:
    """Deterministic fake ModulePort that returns predefined results."""

    def __init__(self, results: list[ModuleResult]) -> None:
        self._results = deque(results)
        self.requests: list[ModuleTaskRequest] = []

    def invoke(self, request: ModuleTaskRequest) -> ModuleResult:
        self.requests.append(request)
        if not self._results:
            raise RuntimeError("scripted ModulePort has no remaining result")
        return self._results.popleft()
