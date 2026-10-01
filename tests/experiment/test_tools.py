from datetime import UTC, datetime
from pathlib import Path
import pytest

from resagent2_contracts import (
    AgentOwner,
    VerificationResult,
    WorkspaceGrant,
    WorkspaceAccess,
    WorkspaceSourceKind,
)
from resagent2_components import (
    EnvironmentBinding,
    EnvironmentManager,
    PreparedEnvironment,
    WorkspaceBoundary,
)
from resagent2_runtime import AgentState

from resagent2_capabilities import RunShellTool


def _state(**memory) -> AgentState:
    now = datetime.now(UTC)
    return AgentState(
        session_id="session_test",
        agent_name="experiment-run",
        owner=AgentOwner.EXPERIMENT,
        run_id="run_test",
        task_id="task_test",
        attempt_number=1,
        created_at=now,
        updated_at=now,
        memory=memory,
    )


def _boundary(root: Path) -> WorkspaceBoundary:
    return WorkspaceBoundary(WorkspaceGrant(root=str(root), source=WorkspaceSourceKind.LOCAL, access=WorkspaceAccess(read_paths=['.'], write_paths=['.'])))


class _FakeRunner:
    def __init__(self, boundary: WorkspaceBoundary) -> None:
        self.boundary = boundary

    def run_shell(self, command, *, log_dir, index, timeout_seconds, argv_prefix=None, extra_env=None):
        stdout_rel = f"{log_dir}/command_{index:02d}.stdout"
        stderr_rel = f"{log_dir}/command_{index:02d}.stderr"
        stdout = Path(stdout_rel)
        stderr = Path(stderr_rel)
        stdout.parent.mkdir(parents=True, exist_ok=True)
        stderr.parent.mkdir(parents=True, exist_ok=True)
        stdout.write_text("ok", encoding="utf-8")
        stderr.write_text("", encoding="utf-8")
        return VerificationResult(
            command=command,
            exit_code=0,
            timed_out=False,
            stdout_path=stdout_rel,
            stderr_path=stderr_rel,
            duration_seconds=0.0,
        )


def _binding(tmp_path: Path, *, certified: bool = False) -> EnvironmentBinding:
    manager = EnvironmentManager(env_root=tmp_path / "envs", conda_exe="conda")
    prefix = tmp_path / "envs" / "resenv_x"
    prefix.mkdir(parents=True, exist_ok=True)
    binding = EnvironmentBinding(manager, run_id="run_test", workspace_id="ws_test")
    binding.current = PreparedEnvironment(
        env_id="resenv_x", prefix=prefix, python_version="3.12"
    )
    binding.certified = certified
    return binding


@pytest.mark.parametrize("writable,allowed", [(False, True), (True, False)])
def test_run_shell_enforces_permission_at_tool_entry(tmp_path, writable, allowed):
    boundary = WorkspaceBoundary(WorkspaceGrant(root=str(tmp_path), source=WorkspaceSourceKind.LOCAL, access=WorkspaceAccess(read_paths=['.'], write_paths=['.']) if writable else WorkspaceAccess(read_paths=['.'], write_paths=[])))
    tool = RunShellTool(_FakeRunner(boundary), _binding(tmp_path, certified=True), timeout_seconds=30, log_dir=str(tmp_path / "commands"), allowed=allowed)
    with pytest.raises(PermissionError):
        tool.execute(_state(), tool.input_model(command="python train.py"))


def test_run_shell_blocks_experiment_when_automatic_audit_fails(tmp_path, monkeypatch) -> None:
    boundary = _boundary(tmp_path)
    tool = RunShellTool(_FakeRunner(boundary), _binding(tmp_path, certified=False), timeout_seconds=30, log_dir=str(tmp_path / "commands"))
    monkeypatch.setattr(tool.binding.manager, "audit", lambda _: {"success": False})

    observation = tool.execute(_state(), tool.input_model(command="python train.py"))

    assert observation.ok is False
    assert observation.value["blocked"] is True
    assert observation.value["reason"] == "environment_audit_failed"
    assert observation.memory_updates["env_audit"]["success"] is False
    assert observation.memory_updates["env_audit"]["environment_information"] == tool.binding.information


@pytest.mark.parametrize("command", [
    "python train.py", "python -m pipeline.run validate", "ls | sort",
    "python -m pip install numpy",
])
def test_run_shell_allows_experiment_after_certification(tmp_path, command) -> None:
    boundary = _boundary(tmp_path)
    tool = RunShellTool(_FakeRunner(boundary), _binding(tmp_path, certified=True), timeout_seconds=30, log_dir=str(tmp_path / "commands"))

    observation = tool.execute(_state(), tool.input_model(command=command))

    assert observation.value["exit_code"] == 0
    assert observation.value["command"] == command
    assert tool.binding.certified is False


@pytest.mark.parametrize("kind", ["prepare", "audit", "setup"])
def test_environment_tools_reject_disabled_operations_before_effects(tmp_path, kind):
    from resagent2_capabilities import PrepareEnvironmentTool, AuditEnvTool, RunSetupTool
    binding = _binding(tmp_path, certified=True)
    if kind == "prepare":
        tool = PrepareEnvironmentTool(binding, allowed=False)
        arguments = tool.input_model()
    elif kind == "audit":
        tool = AuditEnvTool(binding, allowed=False)
        arguments = tool.input_model()
    else:
        tool = RunSetupTool(_FakeRunner(_boundary(tmp_path)), binding,
                            log_dir=str(tmp_path / "logs"), timeout_seconds=30, allowed=False)
        arguments = tool.input_model(command="python -m pip install numpy")
    with pytest.raises(PermissionError):
        tool.execute(_state(), arguments)
    assert binding.certified


def test_run_shell_blocks_without_environment(tmp_path) -> None:
    boundary = _boundary(tmp_path)
    binding = EnvironmentBinding(
        EnvironmentManager(env_root=tmp_path / "envs", conda_exe="conda"),
        run_id="run_test",
        workspace_id="ws_test",
    )
    tool = RunShellTool(_FakeRunner(boundary), binding, timeout_seconds=30, log_dir=str(tmp_path / "commands"))

    observation = tool.execute(_state(), tool.input_model(command="python train.py"))

    assert observation.ok is False
    assert observation.value["reason"] == "no_environment"
