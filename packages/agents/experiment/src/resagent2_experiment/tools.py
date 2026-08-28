"""Experiment command execution and environment certification tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from resagent2_contracts import QuestionDraft
from resagent2_capabilities import (
    ProcessRunner,
    WorkspaceBoundary,
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
        if args and args[0] == "-m" and len(args) >= 2 and args[1].startswith("pip"):
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


def mutates_environment(command: str) -> bool:
    """Return whether a command changes installed packages."""
    try:
        argv = parse_command(command)
    except ValueError:
        return False
    if not argv:
        return False
    executable = Path(argv[0]).name.lower()
    args = [argument.lower() for argument in argv[1:]]
    if executable in {"pip", "pip3"}:
        return bool(args and args[0] in {"install", "uninstall", "upgrade"})
    if executable in {"python", "python3"} and args[:2] == ["-m", "pip"]:
        return len(args) >= 3 and args[2] in {"install", "uninstall", "upgrade"}
    if executable in {"conda", "mamba", "micromamba"}:
        return bool(args and args[0] in {"install", "remove", "update", "create"})
    return False


class RunCommandInput(RuntimeModel):
    """One shell-free command to run inside the prepared environment."""

    command: NonEmptyStr


class RunCommandTool:
    """Execute a shell-free command, gated by certification and confirmation."""

    name = "run_command"
    input_model = RunCommandInput

    def __init__(
        self,
        runner: ProcessRunner,
        *,
        argv_prefix: list[str],
        env_prefix: Path,
        confirm_before_experiment: bool,
        confirmed: bool,
        timeout_seconds: int,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.runner = runner
        self.argv_prefix = argv_prefix
        self.env_prefix = env_prefix
        self.confirm_before_experiment = confirm_before_experiment
        self.confirmed = confirmed
        self.timeout_seconds = timeout_seconds
        self.extra_env = extra_env

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(RunCommandInput, arguments)
        if classify_command(args.command) == "experiment":
            if state.memory.get("env_certified", False) != str(self.env_prefix):
                return ToolObservation(
                    summary="Experiment command blocked: run audit_env first",
                    value={"blocked": True, "reason": "environment not certified"},
                )
            if self.confirm_before_experiment and not self.confirmed:
                return ToolObservation(
                    summary="Experiment confirmation required",
                    value={"blocked": True, "reason": "confirmation required"},
                    question=QuestionDraft(
                        text=f"Confirm running the experiment command: {args.command}",
                        requested_fields=["approve"],
                        reason="confirm_before_experiment is enabled",
                    ),
                )
        index = int(state.memory.get("command_count", 0)) + 1
        result = self.runner.run(
            args.command,
            log_dir=".resagent2/experiment/commands",
            index=index,
            timeout_seconds=self.timeout_seconds,
            argv_prefix=self.argv_prefix,
            extra_env=self.extra_env,
        )
        memory_updates: dict = {"command_count": index}
        if (
            classify_command(args.command) == "experiment"
            and result.exit_code == 0
            and not result.timed_out
        ):
            memory_updates["experiment_success_count"] = (
                int(state.memory.get("experiment_success_count", 0)) + 1
            )
        if mutates_environment(args.command) and result.exit_code == 0:
            memory_updates["env_certified"] = False
        return ToolObservation(
            summary=f"Command exited with code {result.exit_code}",
            value=result.model_dump(mode="json"),
            memory_updates=memory_updates,
        )


_AUDIT_PROBE = """\
import json
import sys

data = {
    "sys_executable": sys.executable,
    "sys_prefix": sys.prefix,
    "python_version": sys.version.split()[0],
}
try:
    import torch
    data["torch"] = {
        "version": torch.__version__,
        "cuda_available": bool(torch.cuda.is_available()),
    }
except Exception as exc:
    data["torch_error"] = str(exc)
print(json.dumps(data))
"""


class AuditEnvInput(RuntimeModel):
    """Empty request that audits the prepared environment."""

    pass


class AuditEnvTool:
    """Probe the prepared environment and update the certification state."""

    name = "audit_env"
    input_model = AuditEnvInput

    def __init__(
        self,
        runner: ProcessRunner,
        boundary: WorkspaceBoundary,
        *,
        argv_prefix: list[str],
        env_prefix: Path,
        timeout_seconds: int,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.runner = runner
        self.boundary = boundary
        self.argv_prefix = argv_prefix
        self.env_prefix = env_prefix
        self.timeout_seconds = timeout_seconds
        self.extra_env = extra_env

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        probe = self.boundary.resolve_system_write(".resagent2/experiment/audit_probe.py")
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text(_AUDIT_PROBE, encoding="utf-8")
        relative = probe.relative_to(self.boundary.root).as_posix()
        result = self.runner.run(
            f"python {relative}",
            log_dir=".resagent2/experiment/audit",
            index=1,
            timeout_seconds=self.timeout_seconds,
            argv_prefix=self.argv_prefix,
            extra_env=self.extra_env,
        )
        if result.exit_code != 0 or result.timed_out:
            return ToolObservation(
                summary="Environment audit failed to run",
                value={"success": False, "exit_code": result.exit_code},
                memory_updates={"env_certified": False},
            )
        try:
            stdout = (self.boundary.root / result.stdout_path).read_text(
                encoding="utf-8", errors="replace"
            )
            data = json.loads(stdout)
        except (OSError, json.JSONDecodeError):
            return ToolObservation(
                summary="Environment audit output was invalid",
                value={"success": False},
                memory_updates={"env_certified": False},
            )
        prefix_ok = (
            Path(str(data.get("sys_prefix", ""))).resolve()
            == self.env_prefix.resolve()
        )
        value = {
            "success": prefix_ok,
            "sys_prefix": data.get("sys_prefix", ""),
            "python_version": data.get("python_version", ""),
            "torch": data.get("torch"),
            "torch_error": data.get("torch_error"),
        }
        return ToolObservation(
            summary=(
                "Environment audit passed"
                if prefix_ok
                else "Environment audit failed: sys.prefix does not match the env"
            ),
            value=value,
            memory_updates={
                "env_certified": str(self.env_prefix) if prefix_ok else False,
                "env_audit": value,
            },
        )
