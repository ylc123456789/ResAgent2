"""The replaceable ModulePort must obey both envelope and Agent routing contracts."""

from resagent2_contracts import RunPermissions, ExecutionLimits
from resagent2_runtime.budget import current_budget

from datetime import UTC, datetime

import pytest

from resagent2_contracts import (
    AgentOwner,
    AgentResult,
    ArtifactCandidate,
    ControlSignal,
    ErrorCode,
    ModuleError,
    ModuleStatus,
    QuestionDraft,
    ResearchRequest,
    RunBudget,
    RunStatus,
    SessionRef,
    SessionStatus,
    TaskProposal,
    TaskStatus,
    WarningRecord,
    WorkflowAgentKind,
    WorkflowProposal,
)
from resagent2_orchestrator import (
    InMemoryRunStore, ModuleBinding, ResearchRun, ScriptedModulePort, WorkflowScheduler,
)


def _session(status=SessionStatus.COMPLETED):
    now = datetime.now(UTC)
    return SessionRef(
        id="session_boundary", module=AgentOwner.EXPERIMENT, state_uri="session://boundary",
        status=status, created_at=now, updated_at=now,
    )




def _execute(tmp_path, result, agent_kind=WorkflowAgentKind.EXPERIMENT, *, charged_calls=0):
    class MeteredPort(ScriptedModulePort):
        def invoke(self, request):
            budget = current_budget()
            for index in range(charged_calls):
                budget.charge("test-module", index)
                budget.usage.complete("test-module", index, "succeeded")
            return super().invoke(request)
    port = MeteredPort([result])
    engine = WorkflowScheduler(
        bindings={agent_kind: ModuleBinding(
            owner=AgentOwner.EXPERIMENT if agent_kind == WorkflowAgentKind.EXPERIMENT else AgentOwner.CODING,
            port=port,
        )},
        store=InMemoryRunStore(), artifact_root=tmp_path / "artifacts", data_root=tmp_path / "data",
    )
    now = datetime.now(UTC)
    engine.store.save(ResearchRun(run_id='run_boundary', request=ResearchRequest(goal='Test ModulePort', budget=RunBudget(max_llm_calls=50, timeout_seconds=60), permissions=RunPermissions(execute_commands=True, prepare_environment=True), execution_limits=ExecutionLimits(max_tasks=3, max_attempts_per_task=2)), status=RunStatus.RUNNING, created_at=now, updated_at=now))
    engine.accept_proposal("run_boundary", WorkflowProposal(
        work_request_id="work_boundary",
        tasks=[TaskProposal(
            id="task_boundary", work_request_id="work_boundary", instruction="Test",
            workflow_agent_kind=agent_kind,
        )],
    ))
    run = engine.run_until_stable("run_boundary")
    assert len(port.requests) == 1
    return engine, run, run.workflow.tasks[0].attempts[0]


@pytest.mark.parametrize("agent_kind", list(WorkflowAgentKind))
@pytest.mark.parametrize("with_warnings", [False, True])
def test_success_for_each_agent_uses_shared_envelope(tmp_path, agent_kind, with_warnings):
    result = AgentResult(
        status=ModuleStatus.COMPLETED_WITH_WARNINGS if with_warnings else ModuleStatus.COMPLETED,
        report="done",  llm_calls=3,
        warnings=[WarningRecord(code="caveat", message="Limited sample")] if with_warnings else [],
    )
    _, run, attempt = _execute(tmp_path, result, agent_kind, charged_calls=3)
    assert run.workflow.tasks[0].status == TaskStatus.COMPLETED
    assert attempt.error is None
    assert run.llm_calls_used == 3




def test_constructed_envelope_is_revalidated(tmp_path):
    result = AgentResult.model_construct(
        status=ModuleStatus.COMPLETED, report="bypassed constructor",
        llm_calls=-10,
    )
    _, run, attempt = _execute(tmp_path, result)
    assert run.workflow.tasks[0].status == TaskStatus.FAILED
    assert attempt.error.code == ErrorCode.CONTRACT_ERROR
    assert attempt.error.retryable is False
    assert run.llm_calls_used == 0


@pytest.mark.parametrize("calls", [True, "3", -1])
def test_raw_usage_must_be_a_nonnegative_integer(tmp_path, calls):
    raw = AgentResult(status="completed", report="Claimed usage").model_dump()
    raw["llm_calls"] = calls
    _, run, attempt = _execute(tmp_path, raw)
    assert run.workflow.tasks[0].status == TaskStatus.FAILED
    assert attempt.error.code == ErrorCode.CONTRACT_ERROR
    assert run.llm_calls_used == 0


@pytest.mark.parametrize("status", [ModuleStatus.FAILED, ModuleStatus.BLOCKED, ModuleStatus.NEEDS_USER_INPUT])
def test_failure_and_pause_use_shared_envelope(tmp_path, status):
    if status == ModuleStatus.NEEDS_USER_INPUT:
        result = AgentResult(
            status=status, report="Need an answer", session=_session(SessionStatus.PAUSED),
            artifacts=[ArtifactCandidate(kind="question", path="question.json", media_type="application/json", summary="Question", content=QuestionDraft(text='Which metric?', requested_fields=['metric']).model_dump_json())], control=ControlSignal(action="ask_user", candidate_index=0),
        )
    else:
        result = AgentResult(
            status=status, report="Cannot execute",
            error=ModuleError(code=ErrorCode.TOOL_FAILED, message="Original error", retryable=False),
        )
    _, run, attempt = _execute(tmp_path, result)
    assert run.workflow.tasks[0].status.value == status.value
    assert attempt.report.startswith(result.report)
    if status != ModuleStatus.NEEDS_USER_INPUT:
        assert attempt.error.code == ErrorCode.TOOL_FAILED


@pytest.mark.parametrize("failed", [False, True])
def test_registration_failure_preserves_diagnostics_calls_and_prior_artifact(tmp_path, failed):
    original = ModuleError(
        code=ErrorCode.TOOL_FAILED, message="Training crashed", retryable=True,
        details={"stderr_tail": "NameError: totla"},
    ) if failed else None
    session = _session(SessionStatus.FAILED if failed else SessionStatus.COMPLETED)
    result = AgentResult(
        status=ModuleStatus.FAILED if failed else ModuleStatus.COMPLETED_WITH_WARNINGS,
        report="Training failed" if failed else "Training finished",

        error=original, session=session, llm_calls=9,
        warnings=[WarningRecord(code="partial_output", message="Only partial evidence available")],
        artifacts=[
            ArtifactCandidate(kind="experiment_result", path="diagnosis.txt", media_type="text/plain",
                              summary="Available diagnostic", content="NameError: totla"),
            ArtifactCandidate(kind="experiment_result", path="missing.json", media_type="application/json",
                              summary="Missing file"),
        ],
    )
    engine, run, attempt = _execute(tmp_path, result, charged_calls=9)
    assert run.workflow.tasks[0].status == TaskStatus.FAILED
    assert run.llm_calls_used == 9
    assert attempt.session == session
    assert attempt.report.startswith(result.report)
    assert run.workflow.tasks[0].warnings == result.warnings
    assert attempt.error.retryable is False
    assert len(attempt.artifact_ids) == len(run.artifacts) == 1
    assert attempt.artifact_ids[0] in run.artifacts
    if failed:
        assert attempt.error.code == original.code
        assert attempt.error.message == original.message
        assert attempt.error.details["stderr_tail"] == "NameError: totla"
        assert "artifact path" in attempt.error.details["artifact_registration_error"]
    else:
        assert attempt.error.code == ErrorCode.ARTIFACT_MISSING
    persisted = engine.run_until_stable(run.run_id)
    assert persisted.llm_calls_used == 9
    assert persisted.workflow.tasks[0].warnings == result.warnings


@pytest.mark.parametrize("missing_output", [False, True])
def test_failed_experiment_keeps_execution_record_when_candidate_is_missing(
    tmp_path, monkeypatch, missing_output,
):
    """Native failure cleanup preserves command receipts through registration."""
    import shlex
    import subprocess
    import sys
    from pathlib import Path

    from resagent2_components import EnvironmentBinding
    from resagent2_components.environment import PreparedEnvironment
    from resagent2_contracts import WorkspaceAccess, WorkspaceSpec
    from resagent2_experiment import NativeExperimentAgent
    from resagent2_orchestrator.handoffs import read_json
    from resagent2_runtime import ScriptedLLMClient

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "train.py").write_text(
        'import sys\nprint("training failed", file=sys.stderr)\nsys.exit(7)\n'
    )

    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(["git", "add", "train.py"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com",
         "commit", "-qm", "baseline"], cwd=workspace, check=True,
    )

    def prepared_binding(*args, **kwargs):
        binding = EnvironmentBinding(*args, **kwargs)
        binding.current = PreparedEnvironment(
            env_id="test_environment", prefix=Path(sys.prefix),
            python_version=sys.version.split()[0],
        )
        binding.certified = True
        binding.argv_prefix = lambda: None
        return binding

    monkeypatch.setattr("resagent2_experiment.agent.EnvironmentBinding", prepared_binding)
    command = f"{shlex.quote(sys.executable)} train.py"
    agent = NativeExperimentAgent(ScriptedLLMClient([
        {"tool": "run_command", "arguments": {"command": command}},
        {"tool": "finish", "arguments": {
            "status": "failed", "report": "Training failed before producing metrics",
            "artifacts": [{
                "kind": "data", "path": "absent.json", "media_type": "application/json",
                "summary": "Requested but absent output",
            }] if missing_output else [],
        }},
    ]))
    results = []

    class Port:
        def invoke(self, request):
            result = agent.invoke(request)
            results.append(result)
            return result

    engine = WorkflowScheduler(
        bindings={"experiment": ModuleBinding(owner=AgentOwner.EXPERIMENT, port=Port())},
        store=InMemoryRunStore(), artifact_root=tmp_path / "artifacts",
        data_root=tmp_path / "data", workspaces={"ws_test": WorkspaceSpec(
            workspace_id="ws_test", source_kind="local", location=str(workspace),
            access=WorkspaceAccess(read_paths=["."], write_paths=["."]),
        )},
    )
    now = datetime.now(UTC)
    run = ResearchRun(
        run_id="run_boundary", status="running",
        request=ResearchRequest(
            goal="Run the provided experiment", permissions=RunPermissions(execute_commands=True),
            budget=RunBudget(max_llm_calls=2, timeout_seconds=30),
            execution_limits=ExecutionLimits(max_tasks=1, max_attempts_per_task=2),
        ), workspaces=engine._resolve_workspaces("run_boundary"),
        created_at=now, updated_at=now,
    )
    engine.store.save(run)
    engine.accept_proposal(run.run_id, WorkflowProposal(
        work_request_id="work_boundary", tasks=[TaskProposal(
            id="task_boundary", work_request_id="work_boundary",
            workflow_agent_kind="experiment", instruction="Run train.py and report the outcome",
        )],
    ))
    run = engine.run_until_stable(run.run_id)
    task = run.workflow.tasks[0]
    assert len(task.attempts) == len(results) == 1
    attempt = task.attempts[0]
    result = results[0]
    state = agent.loop.store.load(attempt.session.id)
    assert task.status == result.status == state.status == "failed"
    expected_code = ErrorCode.BUDGET_EXHAUSTED if missing_output else ErrorCode.AGENT_REPORTED_FAILURE
    assert attempt.error.code == expected_code
    assert attempt.error == result.error
    assert attempt.session == result.session
    assert attempt.report == result.report
    assert run.llm_calls_used == result.llm_calls == state.llm_calls_used == 2
    assert attempt.error.retryable is False
    if missing_output:
        assert "artifact_path_missing" in state.runtime_feedback.summary
        assert "absent.json" in state.runtime_feedback.summary
    else:
        assert state.runtime_feedback is None
    observations = [event.data["value"] for event in state.events
                    if event.type == "observation" and event.tool == "run_command"]
    assert len(observations) == state.memory["command_count"] == 1
    assert observations[0]["exit_code"] == 7
    assert observations[0]["stderr_tail"].strip() == "training failed"
    assert len(attempt.artifact_ids) == len(run.artifacts) == len(result.artifacts) == 1
    record = run.artifacts[attempt.artifact_ids[0]]
    assert record.kind == result.artifacts[0].kind == "execution_record"
    rows = read_json(record)["results"]
    assert len(rows) == 1
    assert rows[0]["command"] == command
    assert rows[0]["exit_code"] == 7
    assert rows[0]["stderr_path"] == observations[0]["stderr_path"]
    assert Path(rows[0]["stderr_path"]).read_text().strip() == "training failed"
    assert engine.load(run.run_id).artifacts[record.id] == record
