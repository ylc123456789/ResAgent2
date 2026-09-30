"""Experiment command execution tools (environment tools are shared)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import BaseModel

from resagent2_components import (
    CommandPermissionDecision,
    EnvironmentBinding,
    ProcessRunner,
    parse_command,
)
from resagent2_runtime import (
    AgentState,
    ToolObservation,
)
from resagent2_runtime.models import NonEmptyStr, RuntimeModel

# Deterministic command classification: never derived from an LLM stage hint.
# This is a WORKFLOW classification (provisioning vs experiment), not a security
# boundary: "setup" commands may still execute build code or cause side effects.
_SETUP_EXECUTABLES = {
    "pip", "pip3", "apt", "apt-get",
    "ls", "find", "rg", "grep", "sed", "cat", "head", "tail", "wc",
    "pwd", "which", "git", "mkdir", "cp", "mv", "ln", "wget", "curl",
    "echo",
}

_PACKAGE_MANAGERS = {"conda", "mamba", "micromamba", "uv", "poetry"}


def classify_command(command: str) -> str:
    """Return ``setup`` or ``experiment`` from the executable (and subcommand)."""
    try:
        argv = parse_command(command)
    except ValueError:
        return "experiment"
    if not argv:
        return "experiment"
    executable = Path(argv[0]).name.lower()
    if executable in _SETUP_EXECUTABLES:
        return "setup"
    if executable in {"python", "python3"}:
        args = argv[1:]
        if args[:2] == ["-m", "pip"]:
            return "setup"
        if args and args[0] in {"--version", "-V"}:
            return "setup"
        return "experiment"
    if executable in _PACKAGE_MANAGERS:
        # `conda run` / `poetry run` / `uv run` run the actual experiment; only the
        # package-management subcommands are provisioning (setup).
        if len(argv) >= 2 and argv[1].lower() == "run":
            return "experiment"
        return "setup"
    return "experiment"


class RunCommandInput(RuntimeModel):
    """One shell-free command to run inside the bound environment."""

    command: NonEmptyStr


class RunCommandTool:
    """Execute a shell-free experiment command, gated by audit and confirmation."""

    name = "run_command"
    input_model = RunCommandInput
    model_guidance = (
        "Each call executes one shell-free experiment command; make additional calls "
        "as the task needs. Use supported entry points and flags verified in code, "
        "documentation or --help. Use file tools for inspection, prepare_environment "
        "to bind the environment, and run_setup for dependency installation. An "
        "uncertified binding is audited after approval and before execution; this "
        "base audit does not validate task or GPU readiness. The receipt preserves "
        "the exit status, stdout/stderr paths and bounded output tails. Read existing "
        "logs rather than rerunning a command solely to obtain a log."
    )

    def __init__(
        self,
        runner: ProcessRunner,
        binding: EnvironmentBinding,
        *,
        timeout_seconds: int,
        extra_env: dict[str, str] | None = None,
        log_dir: str = ".resagent2/experiment/commands",
        allowed: bool = True,
    ) -> None:
        self.runner = runner
        self.binding = binding
        self.timeout_seconds = timeout_seconds
        self.extra_env = extra_env
        self.log_dir = log_dir
        self.allowed = allowed

    @staticmethod
    def check_command(command: str) -> CommandPermissionDecision:
        """Check the tool's command scope without preparing or executing it."""
        if classify_command(command) != "experiment":
            return CommandPermissionDecision(
                allowed=False,
                reason=(
                    "run_command only runs experiment commands; use run_setup "
                    "for dependency installs and the file tools for inspection"
                ),
            )
        return CommandPermissionDecision(allowed=True)

    def _tail(self, path_str: str, *, limit: int = 2000) -> str:
        """Return a bounded tail of a command log, so failures are diagnosable."""
        path = Path(path_str)
        if not path.is_absolute():
            path = self.runner.boundary.root / path
        try:
            return path.read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError:
            return ""

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(RunCommandInput, arguments)
        if not self.allowed:
            raise PermissionError("Process execution is not authorized")
        if not self.runner.boundary.grant.access.unrestricted:
            raise PermissionError("Experiment processes require an unrestricted trusted workspace")
        if self.binding.current is None:
            return ToolObservation(
                summary="No environment prepared; call prepare_environment first",
                ok=False,
                value={"blocked": True, "reason": "no_environment"},
            )
        workflow = self.check_command(args.command)
        if not workflow.allowed:
            return ToolObservation(
                summary=workflow.reason,
                ok=False,
                value={"blocked": True, "reason": "not_an_experiment_command"},
            )
        audit_updates = {}
        if not self.binding.certified:
            audit_updates["env_audit"] = self.binding.audit()
            if not self.binding.certified:
                return ToolObservation(
                    summary="Environment audit failed; experiment command was not executed",
                    ok=False,
                    value={"blocked": True, "reason": "environment_audit_failed", **audit_updates},
                    memory_updates=audit_updates,
                )
        index = int(state.memory.get("command_count", 0)) + 1
        result = self.runner.run(
            args.command,
            log_dir=self.log_dir,
            index=index,
            timeout_seconds=self.timeout_seconds,
            argv_prefix=self.binding.argv_prefix(),
            extra_env=self.extra_env,
        )
        memory_updates: dict = {"command_count": index, **audit_updates}
        value = result.model_dump(mode="json")
        value.update(audit_updates)
        value["stdout_tail"] = self._tail(result.stdout_path)
        value["stderr_tail"] = self._tail(result.stderr_path)
        return ToolObservation(
            summary=f"Command exited with code {result.exit_code}",
            value=value,
            ok=(result.exit_code == 0 and not result.timed_out),
            memory_updates=memory_updates,
        )
