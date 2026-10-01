"""Shell receipts preserve execution facts, changes, and environment freshness."""

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from resagent2_capabilities import RunShellInput, RunShellTool
from resagent2_components import (
    EnvironmentBinding, GitWorkspace, PreparedEnvironment, ProcessRunner, WorkspaceBoundary,
)
from resagent2_contracts import AgentOwner, WorkspaceAccess, WorkspaceGrant
from resagent2_runtime import AgentState


def _state(**memory):
    now = datetime.now(UTC)
    return AgentState(
        session_id="session_shell", agent_name="coding", owner=AgentOwner.CODING,
        run_id="run_shell", task_id="task_shell", attempt_number=1,
        created_at=now, updated_at=now, memory=memory,
    )


def _boundary(root, access=None):
    return WorkspaceBoundary(WorkspaceGrant(
        root=str(root), source="local",
        access=access or WorkspaceAccess(read_paths=["."], write_paths=["."]),
    ))


@pytest.fixture
def binding(monkeypatch):
    class Manager:
        conda_exe = None

        def inspect(self, **kwargs):
            return PreparedEnvironment(
                env_id="env_shell", prefix=Path(sys.prefix), python_version=sys.version.split()[0],
            )

        def audit(self, environment):
            return {"success": True}

    monkeypatch.setattr(
        "resagent2_components.environment.inspect_environment",
        lambda prefix, conda_exe: {"observed_at": "test"},
    )
    result = EnvironmentBinding(Manager(), run_id="run_shell", workspace_id="workspace")
    result.certified = True
    result.information = {"observed_at": "old"}
    return result


def _tool(root, binding, **kwargs):
    return RunShellTool(
        ProcessRunner(_boundary(root)), binding,
        log_dir=str(root / ".resagent2" / "shell"),
        timeout_seconds=kwargs.pop("timeout_seconds", 3),
        **kwargs,
    )


def _repository(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "tracked.txt").write_text("before\n", encoding="utf-8")
    return GitWorkspace(_boundary(root))


def test_shell_input_preserves_script_whitespace():
    script = "\n  cat <<'EOF'\n  unchanged text\nEOF\n\n"
    assert RunShellInput(command=script).command == script


@pytest.mark.parametrize("command", ["", " \t\n", "echo bad\x00"])
def test_shell_input_rejects_empty_or_nul_commands(command):
    with pytest.raises(ValidationError):
        RunShellInput(command=command)


@pytest.mark.parametrize("access,allowed", [
    (WorkspaceAccess(read_paths=["."], write_paths=["."]), False),
    (WorkspaceAccess(read_paths=["."], write_paths=[]), True),
    (WorkspaceAccess(read_paths=["."], write_paths=["src"]), True),
    (WorkspaceAccess(read_paths=["."], write_paths=["."], denied_paths=["secret"]), True),
])
def test_shell_denies_before_audit_or_execution(tmp_path, binding, access, allowed):
    generation = binding.generation
    tool = RunShellTool(
        ProcessRunner(_boundary(tmp_path, access)), binding,
        log_dir=str(tmp_path / ".resagent2"), timeout_seconds=3, allowed=allowed,
    )
    with pytest.raises(PermissionError):
        tool.execute(_state(), RunShellInput(command="touch should_not_exist"))
    assert not (tmp_path / "should_not_exist").exists()
    assert binding.generation == generation
    assert binding.certified


@pytest.mark.parametrize("missing", [True, False])
def test_shell_blocked_environment_does_not_execute(tmp_path, binding, missing):
    binding.certified = False
    generation = binding.generation
    if missing:
        binding.current = None
    else:
        binding.manager.audit = lambda environment: {"success": False}
    result = _tool(tmp_path, binding).execute(
        _state(), RunShellInput(command="touch should_not_exist"),
    )
    assert not result.ok
    assert result.value["reason"] == ("no_environment" if missing else "environment_audit_failed")
    assert not (tmp_path / "should_not_exist").exists()
    assert binding.generation == generation


def test_shell_audits_then_invalidates_before_execution(tmp_path, binding, monkeypatch):
    tool = _tool(tmp_path, binding, extra_env={"SHELL_TEST_INPUT": "bound value"})
    binding.certified = False
    generation = binding.generation
    original_run = tool.runner.run_shell

    def check_execution(script, **kwargs):
        assert not binding.certified
        assert binding.information is None
        assert binding.generation != generation
        return original_run(script, **kwargs)

    monkeypatch.setattr(tool.runner, "run_shell", check_execution)
    script = 'printf "%s\\n" "$SHELL_TEST_INPUT"\n'
    result = tool.execute(_state(), RunShellInput(command=script))
    assert result.ok
    assert result.value["command"] == script
    assert result.value["stdout_tail"] == "bound value\n"
    assert result.value["env_audit"]["success"] is True
    assert result.memory_updates["env_audit"]["success"] is True
    assert result.value["environment_certified"] is False
    assert not binding.certified
    assert binding.information is None


def test_shell_does_not_overwrite_logs_when_state_is_unchanged(tmp_path, binding):
    tool = _tool(tmp_path, binding)
    first = tool.execute(_state(), RunShellInput(command="echo first"))
    second = tool.execute(_state(), RunShellInput(command="echo second"))
    assert first.value["stdout_path"] != second.value["stdout_path"]
    assert Path(first.value["stdout_path"]).read_text() == "first\n"
    assert Path(second.value["stdout_path"]).read_text() == "second\n"
    assert "command_count" not in first.memory_updates
    assert "command_count" not in second.memory_updates


@pytest.mark.parametrize("tail,timeout,expected_exit", [
    ("exit 7", 3, 7),
    ("sleep 2", 0.2, None),
])
def test_shell_records_coding_changes_after_failure_or_timeout(
    tmp_path, binding, tail, timeout, expected_exit,
):
    repository = _repository(tmp_path)
    tool = _tool(tmp_path, binding, repository=repository, timeout_seconds=timeout)
    script = "printf changed > tracked.txt\nprintf diagnostic >&2\n" + tail
    result = tool.execute(_state(edit_revision=4), RunShellInput(command=script))
    assert not result.ok
    assert result.value["changed_paths"] == ["tracked.txt"]
    assert result.memory_updates["edit_revision"] == 5
    assert result.value["stderr_tail"] == "diagnostic"
    assert result.value["command"] == script
    assert Path(result.value["stdout_path"]).is_file()
    if expected_exit is None:
        assert result.value["timed_out"] is True
    else:
        assert result.value["exit_code"] == expected_exit
        assert result.value["timed_out"] is False
    assert not binding.certified


def test_shell_inspection_does_not_create_an_edit_revision(tmp_path, binding):
    repository = _repository(tmp_path)
    result = _tool(tmp_path, binding, repository=repository).execute(
        _state(edit_revision=4), RunShellInput(command="cat tracked.txt"),
    )
    assert result.ok
    assert result.value["changed_paths"] == []
    assert "edit_revision" not in result.memory_updates
    assert result.value["stdout_tail"] == "before\n"


def test_shell_retains_receipt_when_change_diagnostic_fails(tmp_path, binding, monkeypatch):
    repository = _repository(tmp_path)

    def failed_diagnostic(before):
        raise ValueError("cannot inspect repository after command")

    monkeypatch.setattr(repository, "changed_paths_since", failed_diagnostic)
    result = _tool(tmp_path, binding, repository=repository).execute(
        _state(), RunShellInput(command="echo actual-output; exit 9"),
    )
    assert not result.ok
    assert result.value["exit_code"] == 9
    assert result.value["stdout_tail"] == "actual-output\n"
    assert "cannot inspect repository" in result.value["workspace_change_error"]
    assert "changed_paths" not in result.value
    assert not binding.certified
