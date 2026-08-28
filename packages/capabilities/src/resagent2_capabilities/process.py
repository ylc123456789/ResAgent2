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


class CommandPermissionPolicy:
    """Decide whether Agent-chosen commands may run as verification.

    Enforcement is structural (shell-free argv parsing) plus an optional
    executable deny-list. It is a workflow gate, not an OS sandbox.
    """

    def __init__(self, deny_executables: frozenset[str] = frozenset()) -> None:
        self._deny = {name.lower() for name in deny_executables}

    def check(self, commands: list[str]) -> CommandPermissionDecision:
        for command in commands:
            try:
                argv = parse_command(command)
            except UnsafeCommandError as error:
                return CommandPermissionDecision(allowed=False, reason=str(error))
            if argv and Path(argv[0]).name.lower() in self._deny:
                return CommandPermissionDecision(
                    allowed=False, reason=f"executable {argv[0]!r} is denied"
                )
        return CommandPermissionDecision(allowed=True)


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
        environment = os.environ.copy()
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
