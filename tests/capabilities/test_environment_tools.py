from datetime import UTC, datetime
from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    VerificationResult,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSourceKind,
)
from resagent2_capabilities import (
    AuditEnvTool,
    EnvironmentBinding,
    PreparedEnvironment,
    PrepareEnvironmentTool,
    RunSetupTool,
    SetupCommandPolicy,
    WorkspaceBoundary,
)
from resagent2_runtime import AgentState


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


class _FakeManager:
    """Duck-typed EnvironmentManager that never shells out to conda."""

    def __init__(self, root: Path) -> None:
        self.env_root = root
        self.conda_exe = "conda"
        self.prepared: list[str] = []

    def env_id(self, *, run_id: str, workspace_id: str) -> str:
        return "resenv_x"

    def prepare(self, *, run_id: str, workspace_id: str, python_version: str):
        self.prepared.append(python_version)
        prefix = self.env_root / "resenv_x"
        prefix.mkdir(parents=True, exist_ok=True)
        return PreparedEnvironment(
            env_id="resenv_x", prefix=prefix, python_version=python_version
        )

    def audit(self, environment):
        return {
            "success": True,
            "sys_prefix": str(environment.prefix),
            "python_version": environment.python_version,
            "pip_available": True,
            "prefix_match": True,
            "stderr_tail": "",
        }


class _FakeRunner:
    def __init__(self, boundary: WorkspaceBoundary) -> None:
        self.boundary = boundary
        self.argv_prefixes: list = []

    def run(self, command, *, log_dir, index, timeout_seconds, argv_prefix=None, extra_env=None):
        self.argv_prefixes.append(argv_prefix)
        log_root = Path(log_dir)
        if not log_root.is_absolute():
            log_root = self.boundary.root / log_root
        stdout = log_root / f"command_{index:02d}.stdout"
        stderr = log_root / f"command_{index:02d}.stderr"
        stdout.parent.mkdir(parents=True, exist_ok=True)
        stderr.parent.mkdir(parents=True, exist_ok=True)
        stdout.write_text("ok", encoding="utf-8")
        stderr.write_text("", encoding="utf-8")
        return VerificationResult(
            command=command,
            exit_code=0,
            timed_out=False,
            stdout_path=str(stdout),
            stderr_path=str(stderr),
            duration_seconds=0.0,
        )


def _binding(manager: _FakeManager, *, hard_constraint: str | None = None) -> EnvironmentBinding:
    return EnvironmentBinding(
        manager, run_id="run_test", workspace_id="ws_test", hard_constraint=hard_constraint
    )


# ── SetupCommandPolicy ─────────────────────────────────────────────


def test_setup_policy_allows_install_entry_points() -> None:
    policy = SetupCommandPolicy()
    assert policy.check("python -m pip install -r requirements.txt").allowed
    assert policy.check("pip install -e .").allowed
    assert policy.check("uv sync").allowed
    assert policy.check("poetry install").allowed
    assert policy.check("conda env update -f environment.yml").allowed


def test_setup_policy_forbids_destructive_and_prefix_commands() -> None:
    policy = SetupCommandPolicy()
    assert not policy.check("sudo pip install x").allowed
    assert not policy.check("conda create -n foo python=3.12").allowed
    assert not policy.check("conda remove -n foo numpy").allowed
    assert not policy.check("pip install --prefix /tmp numpy").allowed
    assert not policy.check("pip install -p /tmp numpy").allowed
    assert not policy.check("apt install curl").allowed


# ── PrepareEnvironmentTool ─────────────────────────────────────────


def test_prepare_uses_default_when_no_version_given(tmp_path) -> None:
    manager = _FakeManager(tmp_path / "envs")
    tool = PrepareEnvironmentTool(_binding(manager), default_python="3.12")

    observation = tool.execute(_state(), tool.input_model(python_version=None))

    assert observation.ok is True
    assert manager.prepared == ["3.12"]
    assert observation.value["python_version"] == "3.12"


def test_prepare_hard_constraint_cannot_be_overridden(tmp_path) -> None:
    manager = _FakeManager(tmp_path / "envs")
    tool = PrepareEnvironmentTool(_binding(manager, hard_constraint="3.10"))

    observation = tool.execute(_state(), tool.input_model(python_version="3.12"))

    assert observation.ok is False
    assert observation.value["conflict"] is True


def test_prepare_invalid_version_is_recoverable(tmp_path) -> None:
    manager = _FakeManager(tmp_path / "envs")
    tool = PrepareEnvironmentTool(_binding(manager))

    observation = tool.execute(_state(), tool.input_model(python_version="not-a-version"))

    assert observation.ok is False
    assert observation.value["invalid_version"] == "not-a-version"


def test_prepare_version_switch_is_bounded(tmp_path) -> None:
    manager = _FakeManager(tmp_path / "envs")
    tool = PrepareEnvironmentTool(_binding(manager), max_version_switches=2)

    assert tool.execute(_state(), tool.input_model(python_version="3.10")).ok
    assert tool.execute(_state(), tool.input_model(python_version="3.11")).ok
    assert tool.execute(_state(), tool.input_model(python_version="3.12")).ok
    blocked = tool.execute(_state(), tool.input_model(python_version="3.13"))

    assert blocked.ok is False
    assert "version" in blocked.summary


# ── RunSetupTool ───────────────────────────────────────────────────


def test_run_setup_blocks_without_environment(tmp_path) -> None:
    boundary = WorkspaceBoundary(
        WorkspaceGrant(
            root=str(tmp_path),
            mode=WorkspaceMode.READ_WRITE,
            allowed_paths=["."],
            source=WorkspaceSourceKind.LOCAL,
        )
    )
    binding = _binding(_FakeManager(tmp_path / "envs"))
    tool = RunSetupTool(
        _FakeRunner(boundary), binding, log_dir=str(tmp_path / "setup"), timeout_seconds=30
    )

    observation = tool.execute(
        _state(), tool.input_model(command="python -m pip install -r requirements.txt")
    )

    assert observation.ok is False
    assert observation.value["reason"] == "no_environment"


def test_run_setup_rejects_forbidden_command(tmp_path) -> None:
    boundary = WorkspaceBoundary(
        WorkspaceGrant(
            root=str(tmp_path),
            mode=WorkspaceMode.READ_WRITE,
            allowed_paths=["."],
            source=WorkspaceSourceKind.LOCAL,
        )
    )
    manager = _FakeManager(tmp_path / "envs")
    binding = _binding(manager)
    binding.current = manager.prepare(run_id="r", workspace_id="w", python_version="3.12")
    tool = RunSetupTool(
        _FakeRunner(boundary), binding, log_dir=str(tmp_path / "setup"), timeout_seconds=30
    )

    observation = tool.execute(_state(), tool.input_model(command="sudo pip install x"))

    assert observation.ok is False
    assert observation.value["reason"]


def test_run_setup_success_invalidates_certification(tmp_path) -> None:
    boundary = WorkspaceBoundary(
        WorkspaceGrant(
            root=str(tmp_path),
            mode=WorkspaceMode.READ_WRITE,
            allowed_paths=["."],
            source=WorkspaceSourceKind.LOCAL,
        )
    )
    manager = _FakeManager(tmp_path / "envs")
    binding = _binding(manager)
    binding.current = manager.prepare(run_id="r", workspace_id="w", python_version="3.12")
    binding.certified = True
    tool = RunSetupTool(
        _FakeRunner(boundary), binding, log_dir=str(tmp_path / "setup"), timeout_seconds=30
    )

    observation = tool.execute(
        _state(), tool.input_model(command="python -m pip install -r requirements.txt")
    )

    assert observation.ok is True
    assert binding.certified is False


# ── AuditEnvTool ───────────────────────────────────────────────────


def test_audit_blocks_without_environment(tmp_path) -> None:
    binding = _binding(_FakeManager(tmp_path / "envs"))
    tool = AuditEnvTool(binding)

    observation = tool.execute(_state(), tool.input_model())

    assert observation.ok is False
    assert observation.value["reason"] == "no_environment"


def test_audit_certifies_bound_environment(tmp_path) -> None:
    manager = _FakeManager(tmp_path / "envs")
    binding = _binding(manager)
    binding.current = manager.prepare(run_id="r", workspace_id="w", python_version="3.12")
    tool = AuditEnvTool(binding)

    observation = tool.execute(_state(), tool.input_model())

    assert observation.ok is True
    assert binding.certified is True
    assert observation.value["pip_available"] is True
