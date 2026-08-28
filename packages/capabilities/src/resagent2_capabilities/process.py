"""Shell-free process execution with durable stdout/stderr logs."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from resagent2_contracts import VerificationResult

from .workspace import WorkspaceBoundary


class UnsafeCommandError(ValueError):
    """Raised when a command requires shell interpretation."""


_SHELL_TOKENS = frozenset({";", "&&", "||", "|", "&", ">", ">>", "<", "<<"})

_SENSITIVE_ENV_MARKERS = (
    "API_KEY",
    "API_TOKEN",
    "ACCESS_KEY",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "PRIVATE_KEY",
    "AUTH_SOCK",
    "AGENT_PID",
    "_TOKEN",
)


def _sanitized_environment(base: dict[str, str]) -> dict[str, str]:
    """Drop credential-like variables before launching a child process.

    This is a best-effort guard, not a sandbox: it removes API keys, SSH agent
    sockets, cloud credentials and git tokens so a verification command cannot
    inherit them implicitly.
    """
    env = dict(base)
    for name in list(env):
        upper = name.upper()
        if any(marker in upper for marker in _SENSITIVE_ENV_MARKERS):
            env.pop(name, None)
    return env


def parse_command(command: str) -> list[str]:
    """Parse one command to argv and reject every shell composition feature."""
    if "\n" in command or "\r" in command:
        raise UnsafeCommandError(
            "multiline commands and command substitution are forbidden"
        )
    if "$(" in command or "`" in command:
        raise UnsafeCommandError("command substitution is forbidden")
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        argv = list(lexer)
    except ValueError as error:
        raise UnsafeCommandError(f"invalid command quoting: {error}") from error
    if not argv:
        raise UnsafeCommandError("command cannot be empty")
    if any(token in _SHELL_TOKENS for token in argv):
        raise UnsafeCommandError("shell operators are forbidden; declare separate commands")
    return argv


@dataclass(frozen=True, slots=True)
class CommandPermissionDecision:
    """Outcome of a command-permission check."""

    allowed: bool
    reason: str = ""


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


def _descendant_pids(root: int) -> list[int]:
    """Collect descendant PIDs by walking ``/proc/*/stat`` (Linux only)."""
    descendants: list[int] = []
    if not os.path.isdir("/proc"):
        return descendants
    try:
        entries = [entry for entry in os.listdir("/proc") if entry.isdigit()]
    except OSError:
        return descendants
    children: dict[int, list[int]] = {}
    for entry in entries:
        try:
            with open(f"/proc/{entry}/stat", "rb") as handle:
                stat = handle.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        close = stat.rfind(")")
        if close < 0:
            continue
        try:
            pid = int(stat[:close].rsplit("(", 1)[0].strip().split()[-1])
        except (ValueError, IndexError):
            continue
        rest = stat[close + 1 :].split()
        if len(rest) < 2:
            continue
        try:
            ppid = int(rest[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)
    stack = list(children.get(root, []))
    while stack:
        child = stack.pop()
        descendants.append(child)
        stack.extend(children.get(child, []))
    return descendants


def _kill_process_tree(pid: int) -> None:
    """Best-effort SIGKILL of a process group and its descendants (POSIX)."""
    if os.name != "posix":
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    for child in _descendant_pids(pid):
        try:
            os.kill(child, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


class ProcessRunner:
    """Execute argv in a workspace and terminate its process tree on timeout."""

    def __init__(self, boundary: WorkspaceBoundary) -> None:
        self.boundary = boundary

    def run(
        self,
        command: str,
        *,
        log_dir: str,
        index: int,
        timeout_seconds: int,
        argv_prefix: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> VerificationResult:
        argv = [*(argv_prefix or []), *parse_command(command)]
        stdout_path, stderr_path = self._log_paths(log_dir, index)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        started = monotonic()
        timed_out = False
        environment = _sanitized_environment(os.environ.copy())
        environment.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        if extra_env:
            environment.update(extra_env)
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                argv,
                cwd=self.boundary.root,
                stdout=stdout,
                stderr=stderr,
                env=environment,
                start_new_session=os.name == "posix",
            )
            try:
                exit_code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name == "posix":
                    _kill_process_tree(process.pid)
                else:
                    process.kill()
                exit_code = process.wait()
        return VerificationResult(
            command=command,
            exit_code=exit_code,
            timed_out=timed_out,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            duration_seconds=monotonic() - started,
        )

    def _log_paths(self, log_dir: str, index: int) -> tuple[Path, Path]:
        """Resolve stdout/stderr paths.

        An absolute ``log_dir`` writes audit logs outside the workspace (the Run
        data directory); a relative ``log_dir`` stays inside the workspace's
        reserved ``.resagent2`` directory, enforcing the write boundary.
        """
        root = Path(log_dir)
        if root.is_absolute():
            return (
                root / f"command_{index:02d}.stdout",
                root / f"command_{index:02d}.stderr",
            )
        return (
            self.boundary.resolve_system_write(f"{log_dir}/command_{index:02d}.stdout"),
            self.boundary.resolve_system_write(f"{log_dir}/command_{index:02d}.stderr"),
        )
