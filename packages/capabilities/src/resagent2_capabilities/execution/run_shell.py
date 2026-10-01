"""General Bash execution through the shared process and environment components."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from resagent2_components.environment import EnvironmentBinding
from resagent2_components.git import GitWorkspace
from resagent2_components.process import ProcessRunner, output_tail
from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import RuntimeModel


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
        "Run a Linux Bash script in the bound environment and workspace. The runtime "
        "requests single-use user approval for every script. Keep work in the foreground; "
        "background jobs are not managed. Each call "
        "starts a fresh, non-persistent shell with pipefail and without automatic "
        "exit on error; use && or explicit checks when later steps depend on success. "
        "Prefer file tools for reading and editing, run_setup for installation, and "
        "run_verification for Coding checks. This is not a sandbox. Every script "
        "invalidates prior environment certification; it does not become a verified "
        "check merely because it exits zero. Receipts include stdout/stderr paths "
        "and bounded tails; read those logs rather than rerunning to obtain output."
    )

    def __init__(
        self,
        runner: ProcessRunner,
        binding: EnvironmentBinding,
        *,
        timeout_seconds: int,
        log_dir: str,
        extra_env: dict[str, str] | None = None,
        allowed: bool = True,
        repository: GitWorkspace | None = None,
    ) -> None:
        self.runner = runner
        self.binding = binding
        self.timeout_seconds = timeout_seconds
        self.log_dir = log_dir
        self.extra_env = extra_env
        self.allowed = allowed
        self.repository = repository

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(RunShellInput, arguments)
        if not self.allowed:
            raise PermissionError("Process execution is not authorized")
        if not self.runner.boundary.grant.access.unrestricted:
            raise PermissionError("Shell execution requires an unrestricted trusted workspace")
        if self.binding.current is None:
            return ToolObservation(
                summary="No environment prepared; call prepare_environment first",
                ok=False,
                value={"blocked": True, "reason": "no_environment"},
            )
        audit_updates = {}
        if not self.binding.certified:
            audit_updates["env_audit"] = self.binding.audit()
            if not self.binding.certified:
                return ToolObservation(
                    summary="Environment audit failed; shell script was not executed",
                    ok=False,
                    value={"blocked": True, "reason": "environment_audit_failed", **audit_updates},
                    memory_updates=audit_updates,
                )
        before = self.repository.snapshot() if self.repository is not None else None
        argv_prefix = self.binding.argv_prefix()
        # Arbitrary scripts may change dependencies even when they fail.
        self.binding.invalidate()
        result = self.runner.run_shell(
            args.command,
            log_dir=str(Path(self.log_dir) / uuid4().hex),
            index=1,
            timeout_seconds=self.timeout_seconds,
            argv_prefix=argv_prefix,
            extra_env=self.extra_env,
        )
        value = {**result.model_dump(mode="json"), **audit_updates}
        value["environment_certified"] = self.binding.certified
        value["stdout_tail"] = output_tail(self.runner.boundary.root / result.stdout_path)
        value["stderr_tail"] = output_tail(self.runner.boundary.root / result.stderr_path)
        memory_updates = dict(audit_updates)
        if self.repository is not None:
            try:
                changed = self.repository.changed_paths_since(before)
            except Exception as error:
                # The command already ran; a later diagnostic must not erase its receipt.
                value["workspace_change_error"] = f"{type(error).__name__}: {str(error)[:500]}"
            else:
                value["changed_paths"] = changed
                if changed:
                    memory_updates["edit_revision"] = int(state.memory.get("edit_revision", 0)) + 1
        return ToolObservation(
            summary=f"Shell command exited with code {result.exit_code}",
            value=value,
            ok=(result.exit_code == 0 and not result.timed_out),
            memory_updates=memory_updates,
        )
