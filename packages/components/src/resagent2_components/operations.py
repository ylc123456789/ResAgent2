"""Small deterministic command rules, not a script analyzer or OS sandbox."""

from pathlib import Path
import re

from resagent2_runtime.models import PermissionDecision

from .process import parse_command


_FORBIDDEN = frozenset({
    "rm", "rmdir", "sudo", "su", "doas", "shutdown", "reboot", "halt",
    "poweroff", "mkfs", "dd", "fdisk", "parted", "mount", "umount",
    "bash", "sh", "dash", "zsh", "fish", "cmd", "powershell", "pwsh",
})
_READONLY_GIT = frozenset({"status", "diff", "log", "show", "ls-files", "rev-parse"})
_PYTHON_FLAGS = frozenset({"-u", "-B", "-E", "-s", "-S", "-O", "-OO", "-I"})


def command_decision(command: str, boundary) -> PermissionDecision:
    argv = parse_command(command)
    executable = Path(argv[0]).name.lower().removesuffix(".exe")
    if executable in _FORBIDDEN or executable.startswith("mkfs."):
        return PermissionDecision(outcome="deny", reason="This command is forbidden; use file tools for workspace deletion")
    args = argv[1:]
    if executable == "git":
        allowed = bool(args) and args[0] in _READONLY_GIT
        return PermissionDecision(outcome="allow" if allowed else "deny",
                                  reason="Only read-only Git queries are available")
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable):
        while args and args[0] in _PYTHON_FLAGS:
            args = args[1:]
        if args and args[0] in {"-V", "--version", "--help"}:
            return PermissionDecision(outcome="allow")
        if len(args) >= 2 and args[0] == "-m" and args[1] in {"pytest", "unittest", "py_compile", "compileall"}:
            return PermissionDecision(outcome="allow")
        if args and not args[0].startswith("-"):
            target = Path(args[0])
            if target.is_absolute():
                try:
                    target = target.relative_to(boundary.root)
                except ValueError:
                    return PermissionDecision(outcome="deny", reason="Script is outside the granted workspace")
            boundary.resolve_read_file(target.as_posix())
            return PermissionDecision(outcome="allow")
    if executable == "pytest" or (executable in {"cargo", "go", "npm", "pnpm", "yarn"}
                                 and args and args[0] in {"test", "check"}):
        return PermissionDecision(outcome="allow")
    return PermissionDecision(outcome="ask", reason="Confirm this command before execution")
