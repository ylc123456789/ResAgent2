"""Coding verification tools and the test-command workflow policy."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import NonEmptyStr, RuntimeModel

import hashlib
from pathlib import Path
from time import monotonic

from resagent2_contracts import VerificationResult
from resagent2_components.environment import EnvironmentBinding
from resagent2_components.git import GitBaseline, GitWorkspace
from resagent2_components.process import (
    CommandPermissionDecision, ProcessRunner, UnsafeCommandError, parse_command,
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

    @staticmethod
    def _classify(argv: list[str]) -> CommandPermissionDecision:
        executable = Path(argv[0]).name.lower()
        args = [argument.lower() for argument in argv[1:]]
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
        self.permission_policy = permission_policy or VerificationCommandPolicy()
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
        if self.env_binding is not None:
            argv_prefix = self.env_binding.argv_prefix()
            if argv_prefix is None:
                return ToolObservation(
                    summary="No environment prepared; call prepare_environment before verification",
                    ok=False,
                    value={"blocked": True, "reason": "no_environment"},
                )
            if not self.env_binding.certified:
                return ToolObservation(
                    summary="Environment not audited; call audit_env before verification",
                    ok=False,
                    value={"blocked": True, "reason": "not_certified"},
                )
        revision = int(state.memory.get("edit_revision", 0))

        def _digest() -> str:
            diff = self.repository.diff_since(self.baseline)
            return hashlib.sha256(diff.encode("utf-8")).hexdigest()

        before_digest = _digest()
        deadline = monotonic() + self.timeout_seconds
        results: list[VerificationResult] = []
        for index, command in enumerate(args.commands, start=1):
            remaining = deadline - monotonic()
            if remaining <= 0:
                # A command that never ran must still produce a failure record,
                # so a partial verification pass can never be mistaken for
                # success (ADR-0011 §3).
                results.append(
                    VerificationResult(
                        command=command,
                        exit_code=1,
                        timed_out=True,
                        stdout_path=(
                            f"{self.log_root}/revision_{revision}/command_{index:02d}.stdout"
                        ),
                        stderr_path=(
                            f"{self.log_root}/revision_{revision}/command_{index:02d}.stderr"
                        ),
                        duration_seconds=0.0,
                    )
                )
                continue
            results.append(
                self.runner.run(
                    command,
                    log_dir=f"{self.log_root}/revision_{revision}",
                    index=index,
                    timeout_seconds=remaining,
                    argv_prefix=argv_prefix,
                    extra_env=self.extra_env,
                )
            )
        after_digest = _digest()
        workspace_unchanged = before_digest == after_digest
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
                "stdout_tail": (
                    self.runner.boundary.root / result.stdout_path
                ).read_text(encoding="utf-8", errors="replace")[-2_000:]
                if Path(result.stdout_path).exists()
                else "",
                "stderr_tail": (
                    self.runner.boundary.root / result.stderr_path
                ).read_text(encoding="utf-8", errors="replace")[-2_000:]
                if Path(result.stderr_path).exists()
                else "",
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
            },
            memory_updates={
                "verification_revision": revision,
                "verification_results": payload,
                "verification_diff_sha256": after_digest,
                "verification_workspace_unchanged": workspace_unchanged,
                "verification_environment_generation": (
                    self.env_binding.generation if self.env_binding is not None else None
                ),
            },
        )
