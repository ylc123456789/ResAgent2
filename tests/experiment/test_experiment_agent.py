"""The same Experiment entry supports analysis, execution and reporting."""

from e2e.native_fixtures import tool_turns

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from resagent2_contracts import (
    AgentOwner, AgentPermissions, AgentRequest, ArtifactCandidate, ErrorCode,
    ModuleStatus, QuestionDraft, RecordedAnswer, TaskBudget, VerificationResult,
    WorkspaceGrant, WorkspaceAccess, WorkspaceSourceKind,
)
from resagent2_components import PreparedEnvironment
from resagent2_experiment import NativeExperimentAgent
from resagent2_orchestrator import ArtifactRegistry
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
    client = ScriptedLLMClient(tool_turns([
        {"tool": "read_file", "arguments": {"path": "metrics.json"}},
        {"tool": "finish", "arguments": {"report": "The recorded accuracy is 0.9."}},
    ]))
    agent = NativeExperimentAgent(client)
    result = agent.invoke(request(tmp_path, writable=writable, confirm_commands=True))
    assert result.status == ModuleStatus.COMPLETED, result.report
    persisted = agent.loop.store.load(result.session.id)
    assert not any(event.tool == "run_shell" for event in persisted.events)


def test_generic_finish_delivers_named_file(tmp_path):
    (tmp_path / "metrics.json").write_text('{"accuracy": 0.9}')
    result = NativeExperimentAgent(ScriptedLLMClient(tool_turns([{
        "tool": "finish", "arguments": {"report": "Result ready", "artifacts": [{
            "kind": "experiment_result", "path": "metrics.json", "media_type": "application/json",
            "summary": "Accuracy", "output_name": "metrics",
        }]},
    }]))).invoke(request(tmp_path))
    assert result.status == ModuleStatus.COMPLETED, result.report
    assert result.artifacts[0].output_name == "metrics"


def test_command_permission_denied_before_environment_access(tmp_path):
    result = NativeExperimentAgent(ScriptedLLMClient(tool_turns([{'tool': 'run_shell', 'arguments': {'command': 'python train.py'}}]))).invoke(request(tmp_path, writable=True, permissions=AgentPermissions(execute_commands=False, prepare_environment=True)))
    assert result.status == ModuleStatus.FAILED


@pytest.mark.parametrize("confirm_commands", [False, True])
def test_shell_always_requires_approval_before_audit_or_execution(tmp_path, monkeypatch, confirm_commands):
    calls = []
    environment = PreparedEnvironment(
        env_id="resenv_test", prefix=tmp_path / "environment", python_version="3.12",
    )
    manager = SimpleNamespace(conda_exe="conda", inspect=lambda **_: environment)

    def audit(current):
        assert current == environment
        calls.append("audit")
        return {"success": True}

    def run_shell(self, command, **kwargs):
        calls.append(command)
        return VerificationResult(
            command=command, exit_code=0, timed_out=False,
            stdout_path="out.stdout", stderr_path="out.stderr", duration_seconds=0.1,
        )

    manager.audit = audit
    monkeypatch.setattr("resagent2_experiment.agent.EnvironmentManager", lambda **_: manager)
    monkeypatch.setattr("resagent2_components.ProcessRunner.run_shell", run_shell)
    action = {"tool": "run_shell", "arguments": {"command": "ls | sort"}}
    agent = NativeExperimentAgent(ScriptedLLMClient(tool_turns([
        action, action, {"tool": "finish", "arguments": {"report": "Inspected the files."}},
    ])))
    req = request(tmp_path, writable=True, confirm_commands=confirm_commands)
    first = agent.invoke(req)
    assert first.status == ModuleStatus.NEEDS_USER_INPUT, first.report
    assert calls == []
    question = QuestionDraft.model_validate_json(
        next(item.content for item in first.artifacts if item.kind == "question"))
    answer = RecordedAnswer(
        question_id=f"question_{question.action.action_id}", question_text=question.text,
        requested_fields=question.requested_fields, options=question.options,
        values={"approve": "yes"}, answered_at=datetime.now(UTC),
        run_id=req.run_id, task_id=req.task_id, attempt_number=req.attempt_number,
        action=question.action,
    )
    ref = ArtifactRegistry(tmp_path / "artifacts").register_system_artifact(
        ArtifactCandidate(kind="answer", path="answer.json", media_type="application/json",
                          summary="Approve the exact shell command", content=answer.model_dump_json()),
        run_id=req.run_id, source_type="controller_answer",
        task_id=req.task_id, attempt_number=req.attempt_number,
    )
    resumed = agent.invoke(req.model_copy(update={
        "parent_session_id": first.session.id,
        "input_artifacts": [ref], "resume_artifact_ids": [ref.id],
    }))
    assert resumed.status == ModuleStatus.COMPLETED, resumed.report
    assert resumed.session.id == first.session.id
    assert calls == ["audit", "ls | sort"]
    state = agent.loop.store.load(resumed.session.id)
    assert state.pending_action is None
    executed = [event for event in state.events if event.tool == "run_shell"
                and event.type == "observation" and (event.data.get("value") or {}).get("exit_code") == 0]
    assert len(executed) == 1


def test_missing_result_path_cannot_be_claimed(tmp_path):
    result = NativeExperimentAgent(ScriptedLLMClient(tool_turns([{
        "tool": "finish", "arguments": {"report": "Done", "artifacts": [{
            "kind": "experiment_result", "path": "missing.json", "media_type": "application/json",
            "summary": "Missing",
        }]},
    }]))).invoke(request(tmp_path))
    assert result.status == ModuleStatus.FAILED


def test_missing_workspace_is_blocked(tmp_path):
    result = NativeExperimentAgent(ScriptedLLMClient([])).invoke(request(tmp_path, workspace=None))
    assert result.status == ModuleStatus.BLOCKED


def test_explicit_failure_needs_no_fabricated_command_and_keeps_partial_artifacts(tmp_path):
    (tmp_path / "findings.json").write_text('{"missing": "evaluation entry point"}')
    report = "The evaluation entry point is absent; implementation is needed."
    agent = NativeExperimentAgent(ScriptedLLMClient(tool_turns([{
        "tool": "finish", "arguments": {"status": "failed", "report": report, "artifacts": [{
            "kind": "data", "path": "findings.json", "media_type": "application/json",
            "summary": "Inspection findings",
        }]},
    }])))
    result = agent.invoke(request(tmp_path))
    assert result.status == result.session.status == "failed"
    assert result.error.code == ErrorCode.AGENT_REPORTED_FAILURE
    assert result.error.retryable is False
    assert result.report == report
    assert [item.path for item in result.artifacts] == ["findings.json"]
    state = agent.loop.store.load(result.session.id)
    assert not any(event.tool == "run_shell" for event in state.events)
