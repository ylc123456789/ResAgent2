"""General Bash execution through the shared process component."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from resagent2_components.git import GitWorkspace
from resagent2_components.process import ProcessRunner, output_tail
from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import RuntimeModel
from resagent2_runtime.cancellation import OperationCancelled


class RunShellInput(RuntimeModel):
    """One Bash script, preserving whitespace and here-document delimiters."""

    command: str = Field(min_length=1)

    @field_validator("command")
    @classmethod
    def nonempty_command(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("command must contain non-whitespace text")
        if "\x00" in value:
            raise ValueError("command must not contain NUL bytes")
        return value


class RunShellTool:
    """Execute an approved Bash script and preserve its actual outcomes."""

    name = "run_shell"
    input_model = RunShellInput
    model_guidance = (
        "Run a Linux Bash script in the workspace for installation, checks, training "
        "or evaluation. Inspect, select and manage environments with explicit commands; "
        "use the intended interpreter or activate and execute in the same script. The runtime "
        "requests single-use user approval for every script. Keep work in the foreground; "
        "background jobs are not managed. Each call "
        "starts a fresh, non-persistent shell with pipefail and without automatic "
        "exit on error; use && or explicit checks when later steps depend on success. "
        "Activation, exported variables and directory changes do not persist across calls. "
        "Prefer file tools for reading and editing. This is trusted host execution, "
        "not a sandbox. A zero exit code alone does not establish sufficient validation. "
        "Receipts include stdout/stderr paths "
        "and bounded tails; read those logs rather than rerunning to obtain output."
    )

    def __init__(
        self,
        runner: ProcessRunner,
        *,
        timeout_seconds: int,
        log_dir: str,
        extra_env: dict[str, str] | None = None,
        allowed: bool = True,
        repository: GitWorkspace | None = None,
    ) -> None:
        self.runner = runner
        self.timeout_seconds = timeout_seconds
        self.log_dir = log_dir
        self.extra_env = extra_env
        self.allowed = allowed
        self.repository = repository

    def approval_context(self) -> dict:
        """Describe the same shell startup configuration used by the runner."""
        return self.runner.shell_context(extra_env=self.extra_env)

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        return self._execute(state, arguments)

    def execute_prepared(self, state: AgentState, arguments: BaseModel, prepared: dict) -> ToolObservation:
        """Use the approved startup snapshot at the actual process launch."""
        return self._execute(state, arguments, expected_context=prepared)

    def _execute(self, state: AgentState, arguments: BaseModel, *, expected_context: dict | None = None) -> ToolObservation:
        args = cast(RunShellInput, arguments)
        if not self.allowed:
            raise PermissionError("Process execution is not authorized")
        if not self.runner.boundary.grant.access.unrestricted:
            raise PermissionError("Shell execution requires an unrestricted trusted workspace")
        before = self.repository.snapshot() if self.repository is not None else None
        try:
            result = self.runner.run_shell(
                args.command,
                log_dir=str(Path(self.log_dir) / uuid4().hex),
                index=1,
                timeout_seconds=self.timeout_seconds,
                extra_env=self.extra_env,
                expected_context=expected_context,
            )
        except PermissionError as error:
            return ToolObservation(summary=str(error), ok=False,
                                   value={"execution_status": "not_executed"})
        except OperationCancelled as error:
            if before is not None:
                error.details["code_before"] = before.tree_hash
            raise
        value = result.model_dump(mode="json")
        value["stdout_tail"] = output_tail(self.runner.boundary.root / result.stdout_path)
        value["stderr_tail"] = output_tail(self.runner.boundary.root / result.stderr_path)
        if self.repository is not None:
            value["code_before"] = before.tree_hash
            try:
                changed = self.repository.changed_paths_since(before)
                value["code_after"] = self.repository.snapshot().tree_hash
            except OperationCancelled as error:
                # The script completed, but stopping a later Git diagnostic may
                # be unconfirmed. Preserve both facts through the original call.
                error.details["completed_command"] = value
                raise
            except Exception as error:
                # The command already ran; a later diagnostic must not erase its receipt.
                value["workspace_change_error"] = f"{type(error).__name__}: {str(error)[:500]}"
            else:
                value["changed_paths"] = changed
        return ToolObservation(
            summary=f"Shell command exited with code {result.exit_code}",
            value=value,
            ok=(result.exit_code == 0 and not result.timed_out),
        )
