"""The same Experiment entry supports analysis, execution and reporting."""

from pathlib import Path

import pytest

from resagent2_contracts import (
    AgentOwner, AgentPermissions, AgentRequest, ModuleStatus, TaskBudget,
    WorkspaceGrant, WorkspaceAccess, WorkspaceSourceKind,
)
from resagent2_experiment import NativeExperimentAgent
from resagent2_runtime import ScriptedLLMClient


def request(root, *, writable=False, **updates):
    values = dict(run_id='run_experiment', task_id='task_experiment', attempt_number=1, agent=AgentOwner.EXPERIMENT, instruction='Analyze the existing results', budget=TaskBudget(max_llm_calls=8, timeout_seconds=30), workspace=WorkspaceGrant(root=str(root), source=WorkspaceSourceKind.LOCAL, access=WorkspaceAccess(read_paths=['.'], write_paths=['.']) if writable else WorkspaceAccess(read_paths=['.'], write_paths=[])), workspace_id='ws_test', output_dir=str(root.parent / 'out'))
    values.update(updates)
    values.setdefault("permissions", AgentPermissions(execute_commands=True, prepare_environment=True))
    return AgentRequest(**values)


@pytest.mark.parametrize("writable", [False, True])
def test_existing_result_analysis_finishes_without_new_execution(tmp_path, monkeypatch, writable):
    def reject_scan(*args, **kwargs):
        raise AssertionError("analysis must not scan unrelated workspace files")
    monkeypatch.setattr("resagent2_components.WorkspaceBoundary.iter_files", reject_scan)
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}')
    client = ScriptedLLMClient([
        {"tool": "read_file", "arguments": {"path": "metrics.json"}},
        {"tool": "finish", "arguments": {"report": "The recorded accuracy is 0.9."}},
    ])
    agent = NativeExperimentAgent(client)
    result = agent.invoke(request(tmp_path, writable=writable, confirm_commands=True))
    assert result.status == ModuleStatus.COMPLETED, result.report
    persisted = agent.loop.store.load(result.session.id)
    assert persisted.memory["command_count"] == 0


def test_generic_finish_delivers_named_file(tmp_path):
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}')
    result = NativeExperimentAgent(ScriptedLLMClient([{
        "tool": "finish", "arguments": {"report": "Result ready", "artifacts": [{
            "kind": "experiment_result", "path": "metrics.json", "media_type": "application/json",
            "summary": "Accuracy", "output_name": "metrics",
        }]},
    }])).invoke(request(tmp_path))
    assert result.status == ModuleStatus.COMPLETED, result.report
    assert result.artifacts[0].output_name == "metrics"


def test_command_permission_denied_before_environment_access(tmp_path):
    result = NativeExperimentAgent(ScriptedLLMClient([{'tool': 'run_command', 'arguments': {'command': 'python train.py'}}])).invoke(request(tmp_path, writable=True, permissions=AgentPermissions(execute_commands=False, prepare_environment=True)))
    assert result.status == ModuleStatus.FAILED


@pytest.mark.parametrize("command", ["pip install numpy", "python -m pip install numpy", "ls"])
@pytest.mark.parametrize("confirm_commands", [False, True])
def test_non_experiment_command_is_denied_before_approval(tmp_path, monkeypatch, command, confirm_commands):
    def forbidden(*args, **kwargs):
        raise AssertionError("Rejected commands must not reach execution or audit")

    monkeypatch.setattr("resagent2_experiment.tools.RunCommandTool.execute", forbidden)
    monkeypatch.setattr("resagent2_components.EnvironmentBinding.audit", forbidden)
    agent = NativeExperimentAgent(ScriptedLLMClient([
        {"tool": "run_command", "arguments": {"command": command}},
        {"tool": "finish", "arguments": {"report": "No experiment was executed."}},
    ]))
    result = agent.invoke(request(tmp_path, writable=True, confirm_commands=confirm_commands))

    assert result.status == ModuleStatus.COMPLETED, result.report
    assert all(artifact.kind != "question" for artifact in result.artifacts)
    state = agent.loop.store.load(result.session.id)
    assert state.pending_action is None
    assert state.memory["command_count"] == 0
    rejected = [event.data for event in state.events
                if event.type == "observation" and event.tool == "run_command"]
    assert len(rejected) == 1 and rejected[0]["ok"] is False
    assert "run_command only runs experiment commands" in rejected[0]["summary"]


def test_missing_result_path_cannot_be_claimed(tmp_path):
    result = NativeExperimentAgent(ScriptedLLMClient([{
        "tool": "finish", "arguments": {"report": "Done", "artifacts": [{
            "kind": "experiment_result", "path": "missing.json", "media_type": "application/json",
            "summary": "Missing",
        }]},
    }])).invoke(request(tmp_path))
    assert result.status == ModuleStatus.FAILED


def test_missing_workspace_is_blocked(tmp_path):
    result = NativeExperimentAgent(ScriptedLLMClient([])).invoke(request(tmp_path, workspace=None))
    assert result.status == ModuleStatus.BLOCKED
