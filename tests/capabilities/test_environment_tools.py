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
    EnvironmentManagerError,
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

    def inspect(self, *, run_id: str, workspace_id: str):
        return None

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
    assert policy.check("conda env update -f environment.yml").allowed


def test_setup_policy_forbids_uv_and_poetry() -> None:
    policy = SetupCommandPolicy()
    assert not policy.check("uv sync").allowed
    assert not policy.check("poetry install").allowed


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
    state = _state()

    def run(python_version: str):
        observation = tool.execute(state, tool.input_model(python_version=python_version))
        state.memory.update(observation.memory_updates)
        return observation

    assert run("3.10").ok
    assert run("3.11").ok
    assert run("3.12").ok
    blocked = run("3.13")

    assert blocked.ok is False
    assert "version" in blocked.summary


def test_prepare_version_switch_survives_session_restart(tmp_path) -> None:
    manager = _FakeManager(tmp_path / "envs")
    binding = _binding(manager)
    # Simulate a resumed session: the binding restored the existing 3.12 env and
    # two switches already happened before the restart (persisted in memory).
    binding.current = PreparedEnvironment(
        env_id="resenv_x",
        prefix=tmp_path / "envs" / "resenv_x",
        python_version="3.12.4",
    )
    tool = PrepareEnvironmentTool(binding, max_version_switches=2)
    state = _state(version_switches=2, last_requested_python="3.12")

    observation = tool.execute(state, tool.input_model(python_version="3.13"))

    assert observation.ok is False
    assert observation.value["version_switches"] == 3


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


def test_binding_restores_existing_env_but_requires_reaudit() -> None:
    class Manager:
        conda_exe = "conda"

        def inspect(self, *, run_id, workspace_id):
            return PreparedEnvironment(
                env_id="resenv_x", prefix=Path("/tmp/env"), python_version="3.12.4"
            )

    binding = EnvironmentBinding(Manager(), run_id="r", workspace_id="w")

    assert binding.current is not None
    assert binding.current.python_version == "3.12.4"
    assert binding.certified is False  # a restored env must be re-audited


def test_failed_switches_consume_the_switch_budget(tmp_path) -> None:
    class FailingManager(_FakeManager):
        def prepare(self, *, run_id, workspace_id, python_version):
            raise EnvironmentManagerError("creation failed")

    manager = FailingManager(tmp_path / "envs")
    binding = _binding(manager)
    binding.current = PreparedEnvironment(
        env_id="resenv_x",
        prefix=tmp_path / "envs" / "resenv_x",
        python_version="3.12.4",
    )
    tool = PrepareEnvironmentTool(binding, max_version_switches=2)
    state = _state(last_requested_python="3.12", version_switches=0)

    def run(python_version):
        observation = tool.execute(state, tool.input_model(python_version=python_version))
        state.memory.update(observation.memory_updates)
        return observation

    assert run("3.11").ok is False  # switch 1, failed
    assert state.memory["version_switches"] == 1
    assert run("3.10").ok is False  # switch 2, failed
    assert state.memory["version_switches"] == 2
    blocked = run("3.9")  # switch 3 -> rejected by the budget
    assert blocked.ok is False
    assert blocked.value["version_switches"] == 3


def test_conda_update_uses_manager_conda_and_single_prefix(tmp_path) -> None:
    boundary = WorkspaceBoundary(
        WorkspaceGrant(
            root=str(tmp_path),
            mode=WorkspaceMode.READ_WRITE,
            allowed_paths=["."],
            source=WorkspaceSourceKind.LOCAL,
        )
    )
    manager = _FakeManager(tmp_path / "envs")
    manager.conda_exe = "/opt/conda/bin/conda"
    binding = _binding(manager)
    binding.current = manager.prepare(run_id="r", workspace_id="w", python_version="3.12")
    prefix = str(binding.current.prefix)

    class RecordingRunner:
        def __init__(self, boundary):
            self.boundary = boundary
            self.commands = []

        def run(self, command, *, log_dir, index, timeout_seconds, argv_prefix=None, extra_env=None):
            self.commands.append((command, argv_prefix))
            return VerificationResult(
                command=command, exit_code=0, timed_out=False,
                stdout_path=str(tmp_path / "x.stdout"),
                stderr_path=str(tmp_path / "x.stderr"),
                duration_seconds=0.0,
            )

    runner = RecordingRunner(boundary)
    tool = RunSetupTool(runner, binding, log_dir=str(tmp_path / "setup"), timeout_seconds=30)

    observation = tool.execute(
        _state(), tool.input_model(command="conda env update -f environment.yml")
    )

    assert observation.ok is True
    command, argv_prefix = runner.commands[0]
    assert argv_prefix is None  # conda runs at host level, no `conda run` wrapper
    assert command.startswith("/opt/conda/bin/conda ")
    assert command.count(prefix) == 1
    assert "-p " + prefix in command


def test_environment_cleanup_selects_only_managed(tmp_path) -> None:
    import json

    from resagent2_capabilities import EnvironmentManager
    from resagent2_capabilities.environment import _BASE_MARKER

    env_root = tmp_path / "envs"
    manager = EnvironmentManager(env_root=env_root, conda_exe="conda")

    managed = env_root / "resenv_managed"
    managed.mkdir(parents=True)
    (managed / _BASE_MARKER).write_text(
        json.dumps(
            {
                "env_id": "resenv_managed",
                "run_id": "run_a",
                "workspace_id": "ws_a",
                "python_version": "3.12.4",
                "created_at": "2026-08-01T00:00:00+00:00",
                "last_used_at": "2026-08-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    # An unmanaged directory with no ResAgent2 marker must never be selected.
    (env_root / "resenv_unmanaged").mkdir(parents=True)

    listed = manager.list_managed_environments()
    assert [entry["env_id"] for entry in listed] == ["resenv_managed"]

    plan = manager.plan_environment_cleanup(completed_run_ids={"run_a"})
    assert [entry["env_id"] for entry in plan] == ["resenv_managed"]

    deleted = manager.apply_environment_cleanup(plan)
    assert deleted == ["resenv_managed"]
    assert not (env_root / "resenv_managed").exists()
    assert (env_root / "resenv_unmanaged").exists()
