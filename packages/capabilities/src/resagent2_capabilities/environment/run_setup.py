"""Run setup tool."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import NonEmptyStr, RuntimeModel
from resagent2_components.environment import EnvironmentBinding, SetupCommandPolicy
from resagent2_components.process import ProcessRunner, parse_command

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
        allowed: bool = True,
    ) -> None:
        self.runner = runner
        self.binding = binding
        self.log_dir = log_dir
        self.timeout_seconds = timeout_seconds
        self.policy = policy or SetupCommandPolicy()
        self.allowed = allowed

    def _tail(self, path_str: str, *, limit: int = 2000) -> str:
        path = Path(path_str)
        if not path.is_absolute():
            path = self.runner.boundary.root / path
        try:
            return path.read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError:
            return ""

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        if not self.allowed:
            raise PermissionError("Environment setup is not authorized")
        boundary = getattr(self.runner, "boundary", None)
        if boundary is not None and not boundary.grant.access.unrestricted:
            raise PermissionError("Setup processes require an unrestricted trusted workspace")
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
        argv = parse_command(args.command)
        argv_prefix = self.binding.argv_prefix()
        if Path(argv[0]).name.lower() == "conda":
            # conda manages the env from the host: rebuild with the manager's
            # conda and the bound prefix, not the agent-named executable.
            argv = [self.binding.manager.conda_exe, "env", "update", "-p",
                    str(self.binding.current.prefix), *argv[3:]]
            argv_prefix = None
        else:
            # A conda-run wrapper does not replace an explicit interpreter.
            # Resolve pip through the bound Python, independent of PATH.
            python = self.binding.current.prefix / (
                "python.exe" if os.name == "nt" else "bin/python"
            )
            install_args = argv[3:] if argv[0] in {"python", "python3"} else argv[1:]
            argv = [str(python), "-m", "pip", *install_args]
        command = shlex.join(argv)
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
