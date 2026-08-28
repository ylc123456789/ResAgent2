from datetime import UTC, datetime
from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    VerificationResult,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSource,
)
from resagent2_capabilities import WorkspaceBoundary
from resagent2_runtime import AgentState

from resagent2_experiment.tools import (
    AuditEnvTool,
    RunCommandTool,
    classify_command,
    mutates_environment,
)


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
    return WorkspaceBoundary(
        WorkspaceGrant(
            root=str(root),
            mode=WorkspaceMode.READ_WRITE,
            allowed_paths=["."],
            source=WorkspaceSource.EXISTING,
        )
    )


class _FakeRunner:
    def __init__(self, boundary: WorkspaceBoundary, env_prefix: Path) -> None:
        self.boundary = boundary
        self.env_prefix = env_prefix

    def run(self, command, *, log_dir, index, timeout_seconds, argv_prefix=None, extra_env=None):
        stdout_rel = f"{log_dir}/command_{index:02d}.stdout"
        stderr_rel = f"{log_dir}/command_{index:02d}.stderr"
        stdout = self.boundary.resolve_system_write(stdout_rel)
        stderr = self.boundary.resolve_system_write(stderr_rel)
        stdout.parent.mkdir(parents=True, exist_ok=True)
        stderr.parent.mkdir(parents=True, exist_ok=True)
        if "audit_probe.py" in command:
            import json

            stdout.write_text(
                json.dumps({"sys_prefix": str(self.env_prefix), "python_version": "3.12"}),
                encoding="utf-8",
            )
        else:
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


def test_classify_command_is_deterministic() -> None:
    assert classify_command("pip install numpy") == "setup"
    assert classify_command("python -m pip install numpy") == "setup"
    assert classify_command("python --version") == "setup"
    assert classify_command("ls") == "setup"
    assert classify_command("python train.py --epochs 2") == "experiment"
    assert classify_command("./run.sh") == "experiment"
    assert classify_command("python -c \"print(1)\"") == "experiment"


def test_classify_command_does_not_treat_wrapper_run_as_setup() -> None:
    assert classify_command("conda install numpy") == "setup"
    assert classify_command("conda run -p /env python train.py") == "experiment"
    assert classify_command("poetry run python train.py") == "experiment"
    assert classify_command("uv run python train.py") == "experiment"
    assert classify_command("bash -c 'echo hi'") == "experiment"


def test_mutates_environment_detects_package_changes() -> None:
    assert mutates_environment("pip install numpy") is True
    assert mutates_environment("python -m pip uninstall torch") is True
    assert mutates_environment("python train.py") is False
    assert mutates_environment("ls") is False


def test_run_command_blocks_experiment_before_certification(tmp_path) -> None:
    boundary = _boundary(tmp_path)
    tool = RunCommandTool(
        _FakeRunner(boundary, tmp_path),
        argv_prefix=[],
        env_prefix=tmp_path,
        confirm_before_experiment=False,
        confirmed=True,
        timeout_seconds=30,
    )

    observation = tool.execute(_state(), tool.input_model(command="python train.py"))

    assert observation.value["blocked"] is True
    assert "audit_env" in observation.summary


def test_run_command_allows_experiment_after_certification(tmp_path) -> None:
    boundary = _boundary(tmp_path)
    tool = RunCommandTool(
        _FakeRunner(boundary, tmp_path),
        argv_prefix=[],
        env_prefix=tmp_path,
        confirm_before_experiment=False,
        confirmed=True,
        timeout_seconds=30,
    )

    observation = tool.execute(
        _state(env_certified=str(tmp_path)), tool.input_model(command="python train.py")
    )

    assert observation.value["exit_code"] == 0


def test_run_command_asks_for_confirmation(tmp_path) -> None:
    boundary = _boundary(tmp_path)
    tool = RunCommandTool(
        _FakeRunner(boundary, tmp_path),
        argv_prefix=[],
        env_prefix=tmp_path,
        confirm_before_experiment=True,
        confirmed=False,
        timeout_seconds=30,
    )

    observation = tool.execute(
        _state(env_certified=str(tmp_path)), tool.input_model(command="python train.py")
    )

    assert observation.question is not None
    assert observation.question.requested_fields == ["approve"]


def test_audit_env_certifies_matching_prefix(tmp_path) -> None:
    boundary = _boundary(tmp_path)
    env_prefix = tmp_path / "envs" / "resenv_x"
    env_prefix.mkdir(parents=True)
    tool = AuditEnvTool(
        _FakeRunner(boundary, env_prefix),
        boundary,
        argv_prefix=[],
        env_prefix=env_prefix,
        timeout_seconds=30,
    )

    observation = tool.execute(_state(), tool.input_model())

    assert observation.value["success"] is True
    assert observation.memory_updates["env_certified"] == str(env_prefix)
