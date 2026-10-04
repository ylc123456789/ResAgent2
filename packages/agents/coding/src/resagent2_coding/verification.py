"""Coding verification tools and the test-command workflow policy."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from pydantic import BaseModel, Field

from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import NonEmptyStr, RuntimeModel
from resagent2_runtime.budget import DeadlineExceededError

import hashlib
import shlex
from pathlib import Path
from time import monotonic

from resagent2_contracts import VerificationResult
from resagent2_components.environment import EnvironmentBinding
from resagent2_components.git import GitBaseline, GitWorkspace
from resagent2_components.process import (
    CommandPermissionDecision, ProcessRunner, UnsafeCommandError, output_tail, parse_command,
)


_DENY_EXECUTABLES = frozenset(
    {
        "rm", "rmdir", "mv", "curl", "wget", "scp", "ssh", "sftp", "rsync",
        "bash", "sh", "zsh", "dash", "powershell", "cmd", "apt", "apt-get",
        "pip", "pip3", "conda", "mamba", "micromamba",
    }
)
_DENY_GIT_SUBCOMMANDS = frozenset(
    {"clean", "reset", "checkout", "commit", "push", "merge", "rebase", "tag", "fetch"}
)
_VERIFY_PYTHON_MODULES = frozenset({"pytest", "unittest", "py_compile", "compileall"})


class VerificationCommandPolicy:
    """Restrict Agent-chosen verification commands to known test runners.

    This is a default-deny workflow gate: only recognised verification entry
    points (``python -m pytest/unittest/py_compile``, ``pytest``, ``cargo
    test/check``, ``go test``, ``npm/pnpm/yarn test``) are allowed, and clearly
    destructive/package-management/shell/network commands are denied with an
    explicit reason. It is not an OS sandbox — a test command may still execute
    arbitrary project code, which remains a documented limitation.
    """

    def __init__(self, *, bound_environment: bool = False) -> None:
        self.bound_environment = bound_environment

    def check(self, commands: list[str]) -> CommandPermissionDecision:
        for command in commands:
            try:
                argv = parse_command(command)
            except UnsafeCommandError as error:
                return CommandPermissionDecision(allowed=False, reason=str(error))
            decision = self._classify(argv)
            if not decision.allowed:
                return decision
        return CommandPermissionDecision(allowed=True)

    def _classify(self, argv: list[str]) -> CommandPermissionDecision:
        executable = Path(argv[0]).name.lower()
        args = [argument.lower() for argument in argv[1:]]
        if (self.bound_environment and executable in {"python", "python3", "pytest"}
                and argv[0] != executable):
            return CommandPermissionDecision(
                allowed=False,
                reason="Use bare python/python3 or pytest; verification uses the bound interpreter",
            )
        if executable in _DENY_EXECUTABLES:
            return CommandPermissionDecision(
                allowed=False,
                reason=f"executable {argv[0]!r} is not allowed for verification",
            )
        if executable == "git":
            sub = args[0] if args else ""
            if sub in _DENY_GIT_SUBCOMMANDS:
                return CommandPermissionDecision(
                    allowed=False,
                    reason=f"git subcommand {sub!r} is not allowed for verification",
                )
            return CommandPermissionDecision(
                allowed=False, reason="git is not an allowed verification command"
            )
        if executable in {"python", "python3"}:
            if (
                len(args) >= 2
                and args[0] == "-m"
                and args[1] in _VERIFY_PYTHON_MODULES
            ):
                return CommandPermissionDecision(allowed=True)
            return CommandPermissionDecision(
                allowed=False,
                reason="python verification must be 'python -m pytest|unittest|py_compile'",
            )
        if executable == "pytest":
            return CommandPermissionDecision(allowed=True)
        if executable == "cargo":
            if args and args[0] in {"test", "check"}:
                return CommandPermissionDecision(allowed=True)
            return CommandPermissionDecision(
                allowed=False, reason="cargo verification must be 'cargo test|check'"
            )
        if executable == "go":
            if args and args[0] == "test":
                return CommandPermissionDecision(allowed=True)
            return CommandPermissionDecision(
                allowed=False, reason="go verification must be 'go test'"
            )
        if executable in {"npm", "pnpm", "yarn"}:
            if args and args[0] == "test":
                return CommandPermissionDecision(allowed=True)
            return CommandPermissionDecision(
                allowed=False,
                reason=f"{executable} verification must be '{executable} test'",
            )
        return CommandPermissionDecision(
            allowed=False,
            reason=f"executable {argv[0]!r} is not an allowed verification command",
        )


class RunVerificationInput(RuntimeModel):
    """Agent-chosen shell-free commands, run as one bounded verification pass."""

    commands: list[NonEmptyStr] = Field(min_length=1)


class RunVerificationTool:
    """Run Agent-chosen commands and bind results to the edit revision."""

    name = "run_verification"
    input_model = RunVerificationInput
    model_guidance = (
        "Run bounded code-correctness checks in the bound environment. Supported "
        "commands include python -m pytest, unittest, py_compile or compileall; "
        "pytest; cargo test/check; go test; and npm/pnpm/yarn test. Use bare "
        "python/python3 or pytest: Python checks are resolved to the bound interpreter, "
        "not a caller-selected executable path. For import or "
        "device checks, write a unittest with meaningful assertions when no suitable "
        "test exists. python -c and arbitrary scripts are not allowed verification "
        "commands. Do not wrap formal training, fitting or research evaluation in "
        "a test to bypass the Coding/Experiment responsibility boundary. Commands "
        "are shell-free; use run_setup for dependency installation. The tool audits "
        "an uncertified binding after approval and before execution, records actual "
        "results, and tracks whether those results cover the current code and environment."
    )

    def __init__(
        self,
        runner: ProcessRunner,
        repository: GitWorkspace,
        *,
        log_root: str,
        timeout_seconds: int,
        permission_policy: VerificationCommandPolicy | None = None,
        baseline: GitBaseline,
        env_binding: EnvironmentBinding | None = None,
        extra_env: dict[str, str] | None = None,
        allowed: bool = True,
    ) -> None:
        self.runner = runner
        self.repository = repository
        self.log_root = log_root
        self.timeout_seconds = timeout_seconds
        self.permission_policy = permission_policy or VerificationCommandPolicy(
            bound_environment=env_binding is not None,
        )
        self.baseline = baseline
        self.env_binding = env_binding
        self.extra_env = dict(extra_env or {})
        self.allowed = allowed

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(RunVerificationInput, arguments)
        if not self.allowed:
            raise PermissionError("Process execution is not authorized")
        if not self.runner.boundary.grant.access.unrestricted:
            raise PermissionError("Verification processes require an unrestricted trusted workspace")
        decision = self.permission_policy.check(args.commands)
        if not decision.allowed:
            raise ValueError(f"verification commands rejected: {decision.reason}")
        argv_prefix = None
        audit_updates = {}
        if self.env_binding is not None:
            argv_prefix = self.env_binding.argv_prefix()
            if argv_prefix is None:
                return ToolObservation(
                    summary="No environment prepared; call prepare_environment before verification",
                    ok=False,
                    value={"blocked": True, "reason": "no_environment"},
                )
            if not self.env_binding.certified:
                audit_updates["env_audit"] = self.env_binding.audit()
                if not self.env_binding.certified:
                    return ToolObservation(
                        summary="Environment audit failed; verification was not executed",
                        ok=False,
                        value={"blocked": True, "reason": "environment_audit_failed", **audit_updates},
                        memory_updates=audit_updates,
                    )
        revision = int(state.memory.get("edit_revision", 0))
        # A code revision can be verified repeatedly, including after resume.
        execution_id = f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}_{uuid4().hex}"
        log_dir = f"{self.log_root}/revision_{revision}/{execution_id}"

        def _digest() -> str:
            diff = self.repository.diff_since(self.baseline)
            return hashlib.sha256(diff.encode("utf-8")).hexdigest()

        before_digest = _digest()
        deadline = monotonic() + self.timeout_seconds
        results: list[VerificationResult] = []
        unrecorded_commands: list[str] = []
        execution_error = None
        for index, command in enumerate(args.commands, start=1):
            remaining = deadline - monotonic()
            if remaining <= 0:
                unrecorded_commands = list(args.commands[index - 1:])
                execution_error = "Verification batch exceeded its execution time"
                break
            executed_command = command
            if self.env_binding is not None:
                argv = parse_command(command)
                executable = Path(argv[0]).name.lower()
                if executable in {"python", "python3", "pytest"}:
                    python = str(self.env_binding.current.prefix / "bin" / "python")
                    argv = [python, *(
                        ["-m", "pytest", *argv[1:]] if executable == "pytest" else argv[1:]
                    )]
                    executed_command = shlex.join(argv)
            try:
                result = self.runner.run(
                    executed_command,
                    log_dir=log_dir,
                    index=index,
                    timeout_seconds=remaining,
                    argv_prefix=argv_prefix,
                    extra_env=self.extra_env,
                )
            except (DeadlineExceededError, OSError) as error:
                if not results:
                    raise
                # Earlier receipts remain factual. An interrupted runner does
                # not establish whether its current command started.
                unrecorded_commands = list(args.commands[index - 1:])
                execution_error = f"{type(error).__name__}: {str(error)[:500]}"
                break
            results.append(result)
        workspace_error = None
        try:
            after_digest = _digest()
        except Exception as error:
            # The commands already ran; optional freshness diagnostics must
            # not erase their results or extend the shared execution deadline.
            after_digest = None
            workspace_error = f"{type(error).__name__}: {str(error)[:500]}"
        workspace_unchanged = after_digest is not None and before_digest == after_digest
        payload = [result.model_dump(mode="json") for result in results]
        passed = (
            len(results) == len(args.commands)
            and workspace_unchanged
            and all(
                result.exit_code == 0 and not result.timed_out for result in results
            )
        )
        observations = [
            {
                **result.model_dump(mode="json"),
                "stdout_tail": output_tail(self.runner.boundary.root / result.stdout_path),
                "stderr_tail": output_tail(self.runner.boundary.root / result.stderr_path),
            }
            for result in results
        ]
        return ToolObservation(
            summary=(
                f"Verification {'passed' if passed else 'failed'} at revision {revision}; "
                f"workspace_unchanged={workspace_unchanged}"
            ),
            ok=passed,
            value={
                "passed": passed,
                "workspace_unchanged": workspace_unchanged,
                "results": observations,
                "unrecorded_commands": unrecorded_commands,
                "execution_error": execution_error,
                "workspace_error": workspace_error,
                **audit_updates,
            },
            memory_updates={
                **audit_updates,
                "verification_revision": revision,
                "verification_results": payload,
                "verification_unrecorded_commands": unrecorded_commands,
                "verification_execution_error": execution_error,
                "verification_workspace_error": workspace_error,
                "verification_diff_sha256": after_digest,
                "verification_workspace_unchanged": workspace_unchanged,
                "verification_environment_generation": (
                    self.env_binding.generation if self.env_binding is not None else None
                ),
            },
        )
