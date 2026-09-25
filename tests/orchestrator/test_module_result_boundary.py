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


def test_failed_experiment_keeps_execution_record_when_candidate_is_missing(tmp_path):
    """Finalizer ordering and incremental reception preserve the original failure."""
    from resagent2_components import WorkspaceBoundary
    from resagent2_contracts import WorkspaceAccess, WorkspaceGrant
    from resagent2_experiment.completion import ExperimentCompletionCheck
    from resagent2_orchestrator.handoffs import read_json
    from resagent2_runtime import AgentEvent, AgentState, FinishCandidate

    now = datetime.now(UTC)
    state = AgentState(
        session_id="session_boundary", agent_name="experiment",
        owner="experiment", run_id="run_boundary", task_id="task_boundary",
        attempt_number=1, created_at=now, updated_at=now,
    )
    state.events.append(AgentEvent(
        sequence=1, step=1, type="observation", tool="run_command", created_at=now,
        data={"ok": False, "value": {
            "command": "python train.py", "exit_code": 7, "timed_out": False,
            "stdout_path": "train.stdout", "stderr_path": "train.stderr",
            "stderr_tail": "training failed", "duration_seconds": 0.1,
        }},
    ))
    check = ExperimentCompletionCheck(WorkspaceBoundary(WorkspaceGrant(
        root=str(tmp_path), source="local", access=WorkspaceAccess(read_paths=["."]),
    )))
    decision = check.evaluate(state, FinishCandidate(
        report="Training failed before producing metrics",
        artifacts=[ArtifactCandidate(
            kind="data", path="absent.json", media_type="application/json",
            summary="Requested but absent output",
        )],
    ))
    result = AgentResult(
        status="failed", report=decision.report, artifacts=decision.artifacts,
        error=decision.failure, session=_session(SessionStatus.FAILED),
    )
    engine, run, attempt = _execute(tmp_path, result)
    assert run.workflow.tasks[0].status == "failed"
    assert attempt.error.code == ErrorCode.TOOL_FAILED
    assert attempt.error.details["exit_code"] == 7
    assert attempt.error.details["stderr_tail"] == "training failed"
    assert "absent.json" in attempt.error.details["artifact_registration_error"]
    assert attempt.error.retryable is False
    assert len(attempt.artifact_ids) == 1
    record = run.artifacts[attempt.artifact_ids[0]]
    assert record.kind == "execution_record"
    assert read_json(record)["results"][0]["exit_code"] == 7
    assert engine.load(run.run_id).artifacts[record.id] == record
