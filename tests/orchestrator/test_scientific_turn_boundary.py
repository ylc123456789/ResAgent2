"""A returned turn is accepted before any delivery acknowledgement is saved."""

from datetime import UTC, datetime

import pytest

from resagent2_contracts import (
    AgentOwner, ArtifactRef, CapabilityRegistry, ErrorCode, ModuleError,
    QuestionDraft, ResearchRequest, RunBudget, RunStatus, ScientificAssessment,
    ScientificCompletedResult, ScientificFailedResult, ScientificOpinion,
    ScientificQuestionResult, ScientificWorkRequestResult, SessionRef,
    SessionStatus, UserAnswer, WorkOutcome, WorkRequest, WorkRequestDraft, WorkRequestStatus,
    WorkTaskOutcome,
)
from resagent2_orchestrator import (
    CompletionViolationCode, JsonRunStore, ResearchController, ResearchRun,
    ScientificCompletionValidator, WorkflowScheduler,
)


def prepared(tmp_path):
    now = datetime.now(UTC)
    controller = ResearchController(
        scientific_port=None, compiler=None,
        scheduler=WorkflowScheduler(bindings={}, store=JsonRunStore(tmp_path / "runs")),
        registry=CapabilityRegistry(definitions=[]),
    )
    session = SessionRef(
        id="session_scientific_run_boundary", module=AgentOwner.SCIENTIFIC,
        status=SessionStatus.PAUSED, state_uri="session://session_scientific_run_boundary",
        created_at=now, updated_at=now,
    )
    run = ResearchRun(
        run_id="run_boundary", status=RunStatus.RUNNING,
        request=ResearchRequest(goal="Evaluate evidence", budget=RunBudget(
            max_tasks=4, max_attempts_per_task=2, max_llm_calls=20, timeout_seconds=60,
        )),
        scientific_session=session, llm_calls_used=2,
        answers=[UserAnswer(question_id="question_previous", values={"metric": "accuracy"}, answered_at=now)],
        work_requests=[WorkRequest(
            id="work_1", run_id="run_boundary", scientific_session_id=session.id,
            request=WorkRequestDraft(objective="Run experiment", expected_evidence=["result"]),
            status=WorkRequestStatus.STABLE, workflow_revision=1,
            outcome=WorkOutcome(work_request_id="work_1", workflow_revision=1,
                summary="Ready for interpretation", tasks=[WorkTaskOutcome(
                    task_id="task_one", status="completed", summary="Executed experiment",
                )]),
            created_at=now, updated_at=now,
        )],
        created_at=now, updated_at=now,
    )
    controller.scheduler.store.save(run)
    return controller, run


def reply(run, status):
    session_status = {
        "request_work": SessionStatus.PAUSED, "needs_user_input": SessionStatus.PAUSED,
        "completed": SessionStatus.COMPLETED, "failed": SessionStatus.FAILED,
    }[status]
    common = dict(status=status, session=run.scientific_session.model_copy(
        update={"status": session_status}), llm_calls=3)
    if status == "completed":
        return ScientificCompletedResult(**common, opinion=ScientificOpinion(
            verdict="inconclusive", statement="Not enough evidence",
        ))
    if status == "failed":
        return ScientificFailedResult(**common, error=ModuleError(
            code=ErrorCode.TOOL_FAILED, message="DISTINCT_ROOT_CAUSE", retryable=False,
            details={"stderr_tail": "Original diagnostic"},
        ))
    common["assessment"] = ScientificAssessment(statement="Need additional input")
    if status == "needs_user_input":
        return ScientificQuestionResult(**common, question=QuestionDraft(
            text="Which metric?", requested_fields=["metric"], reason="User preference",
        ))
    return ScientificWorkRequestResult(**common, work_request=WorkRequestDraft(
        objective="Repeat experiment", expected_evidence=["result"],
    ))


def assert_unconsumed(run):
    assert run.status == RunStatus.FAILED
    assert run.llm_calls_used == 5
    assert run.work_requests[0].status == WorkRequestStatus.STABLE
    assert run.scientific_session.id == "session_scientific_run_boundary"
    assert run.scientific_session.status == SessionStatus.PAUSED
    assert run.latest_scientific_assessment is None
    assert run.pending_question is None
    assert run.scientific_observed_artifact_ids == []
    assert run.delivered_answer_ids == []


@pytest.mark.parametrize("status", ["request_work", "needs_user_input", "completed", "failed"])
@pytest.mark.parametrize("field,value", [
    ("id", "session_other"), ("module", AgentOwner.CODING), ("status", SessionStatus.ACTIVE),
])
def test_all_branches_reject_wrong_session_before_delivery(tmp_path, status, field, value):
    controller, run = prepared(tmp_path)
    result = reply(run, status)
    result.session = result.session.model_copy(update={field: value})
    actual = controller._apply_turn(run.run_id, result)
    assert_unconsumed(actual)
    assert actual.terminal_error.code == ErrorCode.CONTRACT_ERROR
    assert CompletionViolationCode.INVALID_SESSION in {v.code for v in actual.completion_violations}


@pytest.mark.parametrize("status", ["request_work", "needs_user_input"])
@pytest.mark.parametrize("unknown", [True, False])
def test_assessment_cannot_cite_unknown_or_unobserved_artifact(tmp_path, status, unknown):
    controller, run = prepared(tmp_path)
    result = reply(run, status)
    artifact = ArtifactRef(id="artifact_one", kind="literature_search", run_id=run.run_id,
        producer=AgentOwner.SCIENTIFIC, session_id=run.scientific_session.id,
        uri="file:///not/read/here.json", sha256="a" * 64,
        media_type="application/json", summary="Evidence")
    if not unknown:
        run.artifacts[artifact.id] = artifact
        controller.scheduler.store.save(run)
    result.assessment.evidence_artifact_ids = [artifact.id]
    if unknown:
        result.observed_artifact_ids = [artifact.id]
    actual = controller._apply_turn(run.run_id, result)
    assert_unconsumed(actual)
    expected = CompletionViolationCode.UNKNOWN_EVIDENCE if unknown else CompletionViolationCode.UNOBSERVED_EVIDENCE
    assert expected in {v.code for v in actual.completion_violations}


def test_invalid_schema_is_revalidated_and_not_consumed(tmp_path):
    controller, run = prepared(tmp_path)
    result = reply(run, "completed").model_copy(update={"opinion": {}})
    with pytest.warns(UserWarning, match="serializer warnings"):
        actual = controller._apply_turn(run.run_id, result)
    assert_unconsumed(actual)
    assert actual.terminal_error.code == ErrorCode.CONTRACT_ERROR


def test_valid_question_acknowledges_outcome_once(tmp_path):
    controller, run = prepared(tmp_path)
    actual = controller._apply_turn(run.run_id, reply(run, "needs_user_input"))
    assert actual.status == RunStatus.PAUSED
    assert actual.work_requests[0].status == WorkRequestStatus.CONSUMED
    assert actual.llm_calls_used == 5
    assert actual.pending_question.requested_fields == ["metric"]
    assert actual.delivered_answer_ids == ["question_previous"]
    assert actual.terminal_error is None


@pytest.mark.parametrize("with_session", [False, True])
def test_failed_scientific_turn_keeps_root_cause_on_disk(tmp_path, with_session):
    controller, run = prepared(tmp_path)
    result = reply(run, "failed")
    if not with_session:
        result.session = None
    controller._apply_turn(run.run_id, result)
    persisted = controller.scheduler.store.load(run.run_id)
    assert persisted.status == RunStatus.FAILED
    assert persisted.terminal_error == result.error
    assert persisted.llm_calls_used == 5


def test_final_report_failure_has_durable_reason(tmp_path):
    controller, run = prepared(tmp_path)

    class FailingRegistry:
        def register_final_report(self, *args, **kwargs):
            raise OSError("REPORT_STORAGE_UNAVAILABLE")

    controller.scheduler.artifact_registry = FailingRegistry()
    controller._apply_turn(run.run_id, reply(run, "completed"))
    persisted = controller.scheduler.store.load(run.run_id)
    assert persisted.status == RunStatus.FAILED
    assert "REPORT_STORAGE_UNAVAILABLE" in persisted.terminal_error.message
    assert persisted.llm_calls_used == 5
    assert persisted.final_opinion is None
    assert persisted.final_report_artifact_id is None


def test_required_evidence_is_enforced_by_controller_and_standalone_gate(tmp_path):
    controller, run = prepared(tmp_path)
    run.request.required_evidence_kinds = ["literature_search"]
    controller.scheduler.store.save(run)
    result = reply(run, "completed")
    actual = controller._apply_turn(run.run_id, result)
    assert_unconsumed(actual)
    assert CompletionViolationCode.MISSING_EVIDENCE_KIND in {v.code for v in actual.completion_violations}

    # A standalone caller may have already consumed the work request. It still
    # cannot bypass the Run's evidence requirement by replacing the Native Agent.
    run.work_requests[0].status = WorkRequestStatus.CONSUMED
    validation = ScientificCompletionValidator(CapabilityRegistry(definitions=[])).validate(run, result)
    assert CompletionViolationCode.MISSING_EVIDENCE_KIND in {v.code for v in validation.violations}


def test_registered_observed_import_satisfies_final_gate(tmp_path):
    _, run = prepared(tmp_path)
    run.work_requests[0].status = WorkRequestStatus.CONSUMED
    run.request.required_evidence_kinds = ["literature_search"]
    artifact = ArtifactRef(id="artifact_import", kind="literature_search", run_id=run.run_id,
        producer=AgentOwner.ORCHESTRATOR, metadata={"source_type": "import"},
        uri="file:///frozen/import.json", sha256="a" * 64,
        media_type="application/json", summary="Imported literature")
    run.artifacts[artifact.id] = artifact
    run.scientific_observed_artifact_ids = [artifact.id]
    result = reply(run, "completed")
    result.opinion.evidence_artifact_ids = [artifact.id]
    result.observed_artifact_ids = [artifact.id]
    validation = ScientificCompletionValidator(CapabilityRegistry(definitions=[])).validate(run, result)
    assert validation.ok


@pytest.mark.parametrize("calls,expected", [(3, 3), ("3", 3), (3.0, 3), (True, 1)])
def test_accepted_call_count_uses_validated_value(tmp_path, calls, expected):
    controller, run = prepared(tmp_path)
    raw = reply(run, "needs_user_input").model_dump()
    raw["llm_calls"] = calls
    actual = controller._apply_turn(run.run_id, raw)
    assert actual.status == RunStatus.PAUSED
    assert actual.llm_calls_used == 2 + expected


@pytest.mark.parametrize("changes", [
    {"session": None}, {"status": "failed"}, {"status": "unknown"}, {"opinion": {}},
])
def test_standalone_gate_revalidates_completed_response(tmp_path, changes):
    _, run = prepared(tmp_path)
    run.work_requests[0].status = WorkRequestStatus.CONSUMED
    raw = reply(run, "completed").model_dump()
    raw.update(changes)
    validation = ScientificCompletionValidator(CapabilityRegistry(definitions=[])).validate(run, raw)
    assert not validation.ok
    assert validation.report is None
