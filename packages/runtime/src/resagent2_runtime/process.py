"""Shell-free process execution with durable stdout/stderr logs."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
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


class ProcessRunner:
    """Execute argv in a workspace and terminate its process group on timeout."""

    def __init__(self, boundary: WorkspaceBoundary) -> None:
        self.boundary = boundary

    def run(
        self,
        command: str,
        *,
        log_dir: str,
        index: int,
        timeout_seconds: int,
    ) -> VerificationResult:
        argv = parse_command(command)
        stdout_relative = f"{log_dir}/command_{index:02d}.stdout"
        stderr_relative = f"{log_dir}/command_{index:02d}.stderr"
        stdout_path = self.boundary.resolve_system_write(stdout_relative)
        stderr_path = self.boundary.resolve_system_write(stderr_relative)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        started = monotonic()
        timed_out = False
        environment = os.environ.copy()
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
                exit_code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                exit_code = process.wait()
        return VerificationResult(
            command=command,
            exit_code=exit_code,
            timed_out=timed_out,
            stdout_path=stdout_relative,
            stderr_path=stderr_relative,
            duration_seconds=monotonic() - started,
        )
