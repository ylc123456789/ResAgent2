"""Prepare environment tool."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel

from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import RuntimeModel
from resagent2_components.environment import EnvironmentBinding, EnvironmentManagerError, version_matches

def _valid_python_version(version: str) -> bool:
    parts = version.strip().split(".")
    if len(parts) < 2:
        return False
    return all(part.isdigit() for part in parts) and 0 < int(parts[0]) <= 4
class PrepareEnvironmentInput(RuntimeModel):
    """Agent-chosen Python version; omit to accept the system default."""

    python_version: str | None = None


class PrepareEnvironmentTool:
    """Create or reuse the run/workspace base environment and bind it."""

    name = "prepare_environment"
    input_model = PrepareEnvironmentInput

    def __init__(
        self,
        binding: EnvironmentBinding,
        *,
        default_python: str = "3.12",
        max_version_switches: int = 2,
    ) -> None:
        self.binding = binding
        self.default_python = default_python
        self.max_version_switches = max_version_switches

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(PrepareEnvironmentInput, arguments)
        requested = (args.python_version or "").strip() or None
        hard = self.binding.hard_constraint
        if hard and requested and not version_matches(hard, requested):
            return ToolObservation(
                summary=(
                    f"Python version conflict: requested {requested} but the task "
                    f"requires {hard}"
                ),
                ok=False,
                value={"conflict": True, "requested": requested, "hard_constraint": hard},
            )
        version = requested or hard or self.default_python
        if not _valid_python_version(version):
            return ToolObservation(
                summary=f"Invalid Python version: {version!r}",
                ok=False,
                value={"invalid_version": version},
            )
        # Count a switch against the last *requested* version (not the current
        # binding), so a failed switch still consumes the switch budget.
        last_requested = state.memory.get("last_requested_python")
        switches = int(state.memory.get("version_switches", 0))
        if last_requested is not None and not version_matches(
            version, str(last_requested)
        ):
            switches += 1
            if switches > self.max_version_switches:
                return ToolObservation(
                    summary=(
                        "Too many Python version switches in this attempt "
                        f"(limit {self.max_version_switches})"
                    ),
                    ok=False,
                    value={"version_switches": switches},
                    memory_updates={
                        "version_switches": switches,
                        "last_requested_python": version,
                    },
                )
        # Preparing/switching may mutate the env: invalidate any prior audit.
        self.binding.invalidate()
        try:
            environment = self.binding.manager.prepare(
                run_id=self.binding.run_id,
                workspace_id=self.binding.workspace_id,
                python_version=version,
            )
        except EnvironmentManagerError as error:
            # A failed switch must not leave the old env as the active binding,
            # but must still remember the attempted version.
            self.binding.current = None
            self.binding.certified = False
            return ToolObservation(
                summary=f"Environment creation failed: {error}",
                ok=False,
                value={"stderr_tail": str(error)},
                memory_updates={
                    "version_switches": switches,
                    "last_requested_python": version,
                },
            )
        self.binding.current = environment
        self.binding.certified = False
        return ToolObservation(
            summary=(
                f"Prepared base environment {environment.env_id} "
                f"(Python {environment.python_version})"
            ),
            value={
                "env_id": environment.env_id,
                "prefix": str(environment.prefix),
                "python_version": environment.python_version,
            },
            memory_updates={
                "environment": {
                    "env_id": environment.env_id,
                    "prefix": str(environment.prefix),
                    "python_version": environment.python_version,
                },
                "version_switches": switches,
                "last_requested_python": version,
            },
        )
