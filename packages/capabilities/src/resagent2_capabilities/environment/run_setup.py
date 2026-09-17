"""Run setup tool."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import NonEmptyStr, RuntimeModel
from resagent2_components.environment import EnvironmentBinding, SetupCommandPolicy
from resagent2_components.process import ProcessRunner, UnsafeCommandError, parse_command

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
        # Any setup command may mutate the env even if it later fails, so the
        # previous audit is invalidated *before* the command runs.
        command = args.command
        argv_prefix = self.binding.argv_prefix()
        if _is_conda_command(args.command):
            # conda manages the env from the host: rebuild with the manager's
            # conda and the bound prefix, not the agent-named executable.
            command = _conda_update_command(
                args.command,
                conda_exe=self.binding.manager.conda_exe,
                prefix=self.binding.current.prefix,
            )
            argv_prefix = None
        index = int(state.memory.get("setup_count", 0)) + 1
        self.binding.invalidate()
        result = self.runner.run(
            command,
            log_dir=self.log_dir,
            index=index,
            timeout_seconds=self.timeout_seconds,
            argv_prefix=argv_prefix,
        )
        ok = result.exit_code == 0 and not result.timed_out
        value = result.model_dump(mode="json")
        value["stdout_tail"] = self._tail(result.stdout_path)
        value["stderr_tail"] = self._tail(result.stderr_path)
        return ToolObservation(
            summary=f"Setup command exited with code {result.exit_code}",
            value=value,
            ok=ok,
            memory_updates={"setup_count": index},
        )
def _is_conda_command(command: str) -> bool:
    try:
        argv = parse_command(command)
    except UnsafeCommandError:
        return False
    return bool(argv) and Path(argv[0]).name.lower() == "conda"


def _conda_update_command(command: str, *, conda_exe: str, prefix: Path) -> str:
    """Rebuild ``conda env update`` to use the manager's conda and the bound prefix.

    The command has already passed the policy (argv[0] = conda, argv[1] = env,
    argv[2] = update), so everything after ``update`` is the caller's own args
    and the executable is whatever ``EnvironmentManager.conda_exe`` resolved.
    """
    argv = parse_command(command)
    result = [conda_exe, "env", "update", "-p", str(prefix), *argv[3:]]
    return shlex.join(result)
