"""Shared environment tools: prepare_environment, run_setup, audit_env."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import BaseModel

from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import NonEmptyStr, RuntimeModel

from .environment import EnvironmentBinding, EnvironmentManagerError
from .process import (
    CommandPermissionDecision,
    ProcessRunner,
    UnsafeCommandError,
    parse_command,
)


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
        if hard and requested and requested != hard:
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
        if (
            self.binding.current is not None
            and self.binding.current.python_version != version
        ):
            self.binding.version_switches += 1
            if self.binding.version_switches > self.max_version_switches:
                return ToolObservation(
                    summary=(
                        "Too many Python version switches in this attempt "
                        f"(limit {self.max_version_switches})"
                    ),
                    ok=False,
                    value={"version_switches": self.binding.version_switches},
                )
        try:
            environment = self.binding.manager.prepare(
                run_id=self.binding.run_id,
                workspace_id=self.binding.workspace_id,
                python_version=version,
            )
        except EnvironmentManagerError as error:
            return ToolObservation(
                summary=f"Environment creation failed: {error}",
                ok=False,
                value={"stderr_tail": str(error)},
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
                }
            },
        )


class RunSetupInput(RuntimeModel):
    """One shell-free dependency-installation command."""

    command: NonEmptyStr


class RunSetupTool:
    """Install project dependencies inside the bound environment."""

    name = "run_setup"
    input_model = RunSetupInput

    def __init__(
        self,
        runner: ProcessRunner,
        binding: EnvironmentBinding,
        *,
        log_dir: str,
        timeout_seconds: int,
        policy: "SetupCommandPolicy | None" = None,
    ) -> None:
        self.runner = runner
        self.binding = binding
        self.log_dir = log_dir
        self.timeout_seconds = timeout_seconds
        self.policy = policy or SetupCommandPolicy()

    def _tail(self, path_str: str, *, limit: int = 2000) -> str:
        path = Path(path_str)
        if not path.is_absolute():
            path = self.runner.boundary.root / path
        try:
            return path.read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError:
            return ""

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(RunSetupInput, arguments)
        if self.binding.current is None:
            return ToolObservation(
                summary="No environment prepared; call prepare_environment first",
                ok=False,
                value={"blocked": True, "reason": "no_environment"},
            )
        decision = self.policy.check(args.command)
        if not decision.allowed:
            return ToolObservation(
                summary=f"Setup command rejected: {decision.reason}",
                ok=False,
                value={"blocked": True, "reason": decision.reason},
            )
        index = int(state.memory.get("setup_count", 0)) + 1
        result = self.runner.run(
            args.command,
            log_dir=self.log_dir,
            index=index,
            timeout_seconds=self.timeout_seconds,
            argv_prefix=self.binding.argv_prefix(),
        )
        ok = result.exit_code == 0 and not result.timed_out
        if ok:
            # A successful setup mutates the env, so the previous audit is stale.
            self.binding.certified = False
        value = result.model_dump(mode="json")
        value["stdout_tail"] = self._tail(result.stdout_path)
        value["stderr_tail"] = self._tail(result.stderr_path)
        return ToolObservation(
            summary=f"Setup command exited with code {result.exit_code}",
            value=value,
            ok=ok,
            memory_updates={"setup_count": index},
        )


class AuditEnvInput(RuntimeModel):
    """Empty request that audits the bound base environment."""

    pass


class AuditEnvTool:
    """Prove the bound base environment is correct (framework-agnostic)."""

    name = "audit_env"
    input_model = AuditEnvInput

    def __init__(self, binding: EnvironmentBinding) -> None:
        self.binding = binding

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        if self.binding.current is None:
            return ToolObservation(
                summary="No environment prepared; call prepare_environment first",
                ok=False,
                value={"blocked": True, "reason": "no_environment"},
            )
        audit = self.binding.manager.audit(self.binding.current)
        self.binding.certified = bool(audit.get("success"))
        return ToolObservation(
            summary=(
                "Environment audit passed"
                if audit.get("success")
                else "Environment audit failed: sys.prefix does not match the bound env"
            ),
            value=audit,
            ok=bool(audit.get("success")),
            memory_updates={"env_audit": audit},
        )


class SetupCommandPolicy:
    """Restrict ``run_setup`` to package-installation entry points.

    Default-deny: allows ``python -m pip install ...`` / ``pip install ...``,
    ``uv sync``, ``poetry install``, ``conda env update -f ...``; forbids
    ``sudo``, ``conda create/remove`` and any explicit ``--prefix/-p/--name/-n/
    --target`` (the system binds the environment).
    """

    _FORBIDDEN_FLAGS = {"--prefix", "-p", "--name", "-n", "--target"}

    def check(self, command: str) -> CommandPermissionDecision:
        try:
            argv = parse_command(command)
        except UnsafeCommandError as error:
            return CommandPermissionDecision(allowed=False, reason=str(error))
        executable = Path(argv[0]).name.lower()
        args = [argument.lower() for argument in argv[1:]]
        flag = self._forbidden_flag(args)
        if flag is not None:
            return CommandPermissionDecision(
                allowed=False,
                reason=(
                    f"must not specify {flag} (the system binds the environment "
                    "prefix)"
                ),
            )
        if executable == "sudo":
            return CommandPermissionDecision(allowed=False, reason="sudo is forbidden")
        if executable in {"python", "python3"}:
            if args[:2] == ["-m", "pip"] and len(args) >= 3 and args[2] == "install":
                return CommandPermissionDecision(allowed=True)
            return CommandPermissionDecision(
                allowed=False,
                reason="python setup must be 'python -m pip install ...'",
            )
        if executable in {"pip", "pip3"}:
            if args and args[0] == "install":
                return CommandPermissionDecision(allowed=True)
            return CommandPermissionDecision(
                allowed=False, reason="pip setup must be 'pip install ...'"
            )
        if executable == "uv":
            if args and args[0] == "sync":
                return CommandPermissionDecision(allowed=True)
            return CommandPermissionDecision(
                allowed=False, reason="uv setup must be 'uv sync'"
            )
        if executable == "poetry":
            if args and args[0] == "install":
                return CommandPermissionDecision(allowed=True)
            return CommandPermissionDecision(
                allowed=False, reason="poetry setup must be 'poetry install'"
            )
        if executable in {"conda", "mamba", "micromamba"}:
            if args and args[0] == "env" and "update" in args:
                return CommandPermissionDecision(allowed=True)
            if args and args[0] in {"create", "remove"}:
                return CommandPermissionDecision(
                    allowed=False, reason="conda create/remove is forbidden"
                )
            return CommandPermissionDecision(
                allowed=False,
                reason="conda setup must be 'conda env update -f ...'",
            )
        return CommandPermissionDecision(
            allowed=False,
            reason=f"executable {argv[0]!r} is not an allowed setup command",
        )

    @staticmethod
    def _forbidden_flag(args: list[str]) -> str | None:
        for argument in args:
            if argument in SetupCommandPolicy._FORBIDDEN_FLAGS:
                return argument
            if argument.startswith(("--prefix=", "--name=", "--target=")):
                return argument
        return None
