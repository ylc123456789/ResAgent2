"""Shared argv and explicit Linux shell execution with durable process logs."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep

from resagent2_contracts import VerificationResult
from resagent2_runtime.budget import current_budget, remaining_timeout
from resagent2_runtime.cancellation import OperationCancelled, check_cancelled

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



def _process_identity(pid: int) -> str | None:
    """Use Linux start time to prevent an observed PID being reused for cleanup."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19]
    except (OSError, IndexError):
        return None


def _remember_descendants(process: subprocess.Popen, identities: dict[int, str]) -> None:
    # After reaping, this bare PID can be reused. Only remembered identities and
    # this invocation's output pipes may identify children from that point on.
    if process.returncode is not None:
        return
    for pid in _descendant_pids(process.pid):
        identity = _process_identity(pid)
        if identity is not None:
            identities.setdefault(pid, identity)


def _remember_pipe_owners(process: subprocess.Popen, identities: dict[int, str]) -> None:
    """Find this command's inherited pipes even after its parent has exited.

    These pipe identities are unique to this Popen invocation. A detached child
    can retain them and keep communicate() waiting after being reparented, when
    walking the original parent's current descendants can no longer find it.
    """
    targets = set()
    for stream in (process.stdout, process.stderr):
        if stream is None:
            continue
        try:
            target = os.readlink(f"/proc/self/fd/{stream.fileno()}")
        except (OSError, ValueError):
            continue
        if target.startswith("pipe:["):
            targets.add(target)
    if not targets:
        return
    try:
        entries = os.listdir("/proc")
    except OSError:
        return
    for entry in entries:
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        pid = int(entry)
        identity = _process_identity(pid)
        if identity is None:
            continue
        try:
            descriptors = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for descriptor in descriptors:
            try:
                target = os.readlink(f"/proc/{pid}/fd/{descriptor}")
            except OSError:
                continue
            if target in targets:
                try:
                    info = Path(f"/proc/{pid}/fdinfo/{descriptor}").read_text()
                    flags = next(line.split()[1] for line in info.splitlines()
                                 if line.startswith("flags:"))
                    writable = int(flags, 8) & os.O_ACCMODE in {os.O_WRONLY, os.O_RDWR}
                except (OSError, ValueError, IndexError, StopIteration):
                    continue
                # Only inherited writers own the command output; a reader must
                # never be stopped. Recheck identity after scanning descriptors.
                if writable and _process_identity(pid) == identity:
                    identities.setdefault(pid, identity)
                if writable:
                    break


def _owned_process_running(pid: int, identity: str) -> bool:
    return _process_identity(pid) == identity and _running_pid(pid)


def _running_pid(pid: int) -> bool:
    """A zombie cannot execute; the owner/init will reap it separately."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    except OSError:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _stop_process(process: subprocess.Popen, identities: dict[int, str] | None = None) -> bool:
    """Stop the session and remembered/pipe-owning descendants, then confirm."""
    identities = identities if identities is not None else {}
    if os.name == "posix":
        _remember_descendants(process, identities)
        _remember_pipe_owners(process, identities)
        # The original group is safe while its unreaped leader is active or an
        # identified owned member still belongs to it. Do not signal a recycled
        # bare PID/group once all original members have disappeared.
        group_owned = process.poll() is None
        if not group_owned:
            for pid, identity in identities.items():
                try:
                    if _process_identity(pid) == identity and os.getpgid(pid) == process.pid:
                        group_owned = True
                        break
                except (ProcessLookupError, PermissionError, OSError):
                    pass
        if group_owned:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        for pid, identity in identities.items():
            if _process_identity(pid) == identity:
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
    else:
        process.kill()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        return False
    until = monotonic() + 3
    while any(_owned_process_running(pid, identity) for pid, identity in identities.items()):
        if monotonic() >= until:
            return False
        sleep(0.01)
    return True


def _wait_process(process: subprocess.Popen, timeout: float, identities: dict[int, str]) -> int:
    deadline = monotonic() + timeout
    while True:
        _remember_descendants(process, identities)
        # Once the operation completed, retain its real result even when an
        # interrupt arrives concurrently at the next dispatch boundary.
        if process.poll() is not None:
            return process.returncode
        check_cancelled()
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout)
        try:
            return process.wait(timeout=min(0.05, remaining))
        except subprocess.TimeoutExpired:
            pass


def run_process(command, *, timeout: float = 3600, cwd=None, env=None,
                capture_output: bool = True, text: bool = True, check: bool = False):
    """Bound fixed framework commands by the shared deadline/cancellation scope."""
    check_cancelled()
    timeout = remaining_timeout(timeout)
    process = subprocess.Popen(
        command, cwd=cwd, env=env, text=text,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        start_new_session=os.name == "posix",
    )
    deadline = monotonic() + timeout
    identities: dict[int, str] = {}
    try:
        try:
            while True:
                _remember_descendants(process, identities)
                process.poll()  # Reap an exited parent even if inherited pipes remain open.
                check_cancelled()
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    stdout, stderr = process.communicate(timeout=min(0.05, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pass
        except BaseException as error:
            stopped = _stop_process(process, identities)
            stdout, stderr = None, None
            cleanup_error = None
            if stopped:
                try:
                    stdout, stderr = process.communicate(timeout=3)
                except subprocess.TimeoutExpired as drain_error:
                    # Inherited pipes still being held means cessation was not
                    # confirmed. Preserve the original cancellation and its
                    # truthful failure status instead of masking it with timeout.
                    stopped = False
                    stdout, stderr = drain_error.output, drain_error.stderr
                    cleanup_error = "command output pipes did not close after cleanup"
            if isinstance(error, OperationCancelled):
                def tail(value):
                    if isinstance(value, bytes):
                        value = value.decode("utf-8", errors="replace")
                    return (value or "")[-2000:]

                raise OperationCancelled(error.reason, stopped=stopped, details={
                    "operation": "process", "command": command, "pid": process.pid,
                    "exit_code": process.returncode,
                    "stdout_tail": tail(stdout), "stderr_tail": tail(stderr),
                    "files_retained": True,
                    **({"cleanup_error": cleanup_error} if cleanup_error else {}),
                    "unconfirmed_processes": [pid for pid, identity in identities.items()
                                              if _owned_process_running(pid, identity)],
                }) from None
            if isinstance(error, subprocess.TimeoutExpired) and current_budget() is not None:
                current_budget().remaining_timeout()
            raise
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
    finally:
        # Popen.__exit__ waits without a bound. If stop confirmation failed,
        # return that fact to the Controller instead of hanging in __exit__.
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


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
        check_cancelled()
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
            identities: dict[int, str] = {}
            try:
                exit_code = _wait_process(process, remaining_timeout(timeout_seconds), identities)
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name == "posix":
                    _kill_process_tree(process.pid)
                else:
                    process.kill()
                exit_code = process.wait()
            except BaseException as error:
                stopped = _stop_process(process, identities)
                if isinstance(error, OperationCancelled):
                    raise OperationCancelled(error.reason, stopped=stopped, details={
                        "operation": "process", "command": command, "pid": process.pid,
                        "exit_code": process.returncode,
                        "unconfirmed_processes": [pid for pid, identity in identities.items()
                                                  if _owned_process_running(pid, identity)],
                        "stdout_path": str(stdout_path), "stderr_path": str(stderr_path),
                        "duration_seconds": monotonic() - started,
                        "files_retained": True,
                    }) from None
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
