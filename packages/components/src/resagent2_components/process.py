"""Shared argv and explicit Linux shell execution with durable process logs."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from resagent2_contracts import VerificationResult
from resagent2_runtime.budget import current_budget, remaining_timeout

from .workspace import WorkspaceBoundary


class UnsafeCommandError(ValueError):
    """Raised when a command requires shell interpretation."""


_SHELL_TOKENS = frozenset({";", "&&", "||", "|", "&", ">", ">>", "<", "<<"})

_SHELL_ENVIRONMENT = frozenset({"BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "CDPATH"})

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


def _sanitized_environment(base: dict[str, str], *, shell: bool = False) -> dict[str, str]:
    """Drop credential-like variables before launching a child process.

    This is a best-effort guard, not a sandbox: it removes API keys, SSH agent
    sockets, cloud credentials and git tokens so a verification command cannot
    inherit them implicitly.
    """
    env = dict(base)
    for name in list(env):
        upper = name.upper()
        if (any(marker in upper for marker in _SENSITIVE_ENV_MARKERS)
                or shell and (name in _SHELL_ENVIRONMENT or name.startswith("BASH_FUNC_"))):
            env.pop(name, None)
    return env


def output_tail(path: str | Path, *, limit: int = 2000) -> str:
    """Read a bounded UTF-8 log tail without loading the complete log."""
    if limit <= 0:
        return ""
    try:
        with Path(path).open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - limit * 4))
            return handle.read(limit * 4).decode("utf-8", errors="replace")[-limit:]
    except OSError:
        return ""


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
    descendants = _descendant_pids(pid)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    for child in descendants:
        try:
            os.kill(child, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def run_process(command, *, timeout: float = 3600, cwd=None, env=None,
                capture_output: bool = True, text: bool = True, check: bool = False):
    """Bound fixed framework commands by the same deadline and process cleanup."""
    timeout = remaining_timeout(timeout)
    with subprocess.Popen(command, cwd=cwd, env=env, text=text,
                          stdout=subprocess.PIPE if capture_output else None,
                          stderr=subprocess.PIPE if capture_output else None,
                          start_new_session=os.name == "posix") as process:
        try:
            stdout, stderr = process.communicate(timeout=remaining_timeout(timeout))
        except BaseException as error:
            if os.name == "posix":
                _kill_process_tree(process.pid)
            else:
                process.kill()
            process.communicate()
            if isinstance(error, subprocess.TimeoutExpired) and current_budget() is not None:
                current_budget().remaining_timeout()
            raise
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result


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
        return self._run_argv(
            [*(argv_prefix or []), *parse_command(command)], command=command,
            log_dir=log_dir, index=index, timeout_seconds=timeout_seconds,
            extra_env=extra_env,
        )

    def run_shell(
        self,
        script: str,
        *,
        log_dir: str,
        index: int,
        timeout_seconds: int,
        argv_prefix: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> VerificationResult:
        """Run an exact script in fixed non-login Bash, with pipeline failures retained."""
        if sys.platform != "linux":
            raise RuntimeError("Shell execution is supported only on Linux")
        if not script.strip() or "\x00" in script:
            raise ValueError("shell script must be nonempty and contain no NUL bytes")
        return self._run_argv(
            [*(argv_prefix or []), "/bin/bash", "--noprofile", "--norc",
             "-o", "pipefail", "-c", script], command=script,
            log_dir=log_dir, index=index, timeout_seconds=timeout_seconds,
            extra_env=extra_env, shell=True,
        )

    def _run_argv(
        self,
        argv: list[str],
        *,
        command: str,
        log_dir: str,
        index: int,
        timeout_seconds: int,
        extra_env: dict[str, str] | None,
        shell: bool = False,
    ) -> VerificationResult:
        timeout_seconds = remaining_timeout(timeout_seconds)
        stdout_path, stderr_path = self._log_paths(log_dir, index)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        started = monotonic()
        timed_out = False
        environment = os.environ.copy()
        if extra_env:
            environment.update(extra_env)
        environment = _sanitized_environment(environment, shell=shell)
        environment.setdefault("PYTHONDONTWRITEBYTECODE", "1")
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
                exit_code = process.wait(timeout=remaining_timeout(timeout_seconds))
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name == "posix":
                    _kill_process_tree(process.pid)
                else:
                    process.kill()
                exit_code = process.wait()
            except BaseException:
                if os.name == "posix":
                    _kill_process_tree(process.pid)
                else:
                    process.kill()
                process.wait()
                raise
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
