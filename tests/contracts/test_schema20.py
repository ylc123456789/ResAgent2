"""Contract tests for the schema 2.0 Phase 7 target types (CONTRACTS §20)."""

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from resagent2_contracts import (
    AgentOwner,
    ArtifactId,
    ArtifactRef,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    ErrorCode,
    ResearchRequest,
    RunBudget,
    RunId,
    ScientificAssessment,
    ScientificCompletedResult,
    ScientificFailedResult,
    ScientificOpinion,
    ScientificQuestionResult,
    ScientificTurnRequest,
    ScientificTurnResult,
    ScientificVerdict,
    ScientificWorkRequestResult,
    SessionRef,
    SessionStatus,
    TaskBudget,
    WorkOutcome,
    WorkRequest,
    WorkRequestDraft,
    WorkRequestStatus,
    WorkTaskOutcome,
    QuestionDraft,
    UserAnswer,
)

NOW = datetime(2026, 8, 28, tzinfo=UTC)


def research_request() -> ResearchRequest:
    return ResearchRequest(
        goal="Evaluate the method",
        budget=RunBudget(
            max_tasks=8,
            max_attempts_per_task=2,
            max_llm_calls=20,
            timeout_seconds=3600,
        ),
    )


def session_ref(status: SessionStatus = SessionStatus.PAUSED) -> SessionRef:
    return SessionRef(
        id="session_sci",
        module=AgentOwner.SCIENTIFIC,
        state_uri="memory://sci",
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def work_request(status: WorkRequestStatus = WorkRequestStatus.REQUESTED) -> WorkRequest:
    fields = {
        "id": "work_round1",
        "run_id": "run_example",
        "scientific_session_id": "session_sci",
        "request": WorkRequestDraft(
            objective="Measure the method",
            expected_evidence=["validation_accuracy"],
        ),
        "status": status,
        "created_at": NOW,
        "updated_at": NOW,
    }
    if status in {
        WorkRequestStatus.EXECUTING,
        WorkRequestStatus.STABLE,
        WorkRequestStatus.CONSUMED,
    }:
        fields["workflow_revision"] = 1
    if status in {WorkRequestStatus.STABLE, WorkRequestStatus.CONSUMED}:
        fields["outcome"] = WorkOutcome(
            work_request_id="work_round1",
            workflow_revision=1,
            summary="experiment completed",
            tasks=[
                WorkTaskOutcome(
                    task_id="task_experiment",
                    status="completed",
                    summary="ran experiment",
                )
            ],
        )
    if status == WorkRequestStatus.FAILED:
        fields["error"] = ModuleError(
            code=ErrorCode.CONTRACT_ERROR,
            message="compile failed",
            retryable=False,
        )
    return WorkRequest(**fields)


def test_work_request_round_trips() -> None:
    for status in WorkRequestStatus:
        request = work_request(status)
        restored = WorkRequest.model_validate_json(request.model_dump_json())
        assert restored == request
        assert restored.status == status


def test_work_request_rejects_execution_fields_when_requested() -> None:
    with pytest.raises(ValidationError, match="cannot carry execution fields"):
        WorkRequest(
            id="work_round1",
            run_id="run_example",
            scientific_session_id="session_sci",
            request=WorkRequestDraft(
                objective="Measure",
                expected_evidence=["accuracy"],
            ),
            status=WorkRequestStatus.REQUESTED,
            workflow_revision=1,
            created_at=NOW,
            updated_at=NOW,
        )


def test_work_request_rejects_execution_fields_from_llm() -> None:
    """WorkRequestDraft must reject capability/task/path/env/status fields."""
    with pytest.raises(ValidationError):
        WorkRequestDraft(
            objective="Measure",
            expected_evidence=["accuracy"],
            capability="experiment_run",
        )
    with pytest.raises(ValidationError):
        WorkRequestDraft(
            objective="Measure",
            expected_evidence=["accuracy"],
            task_id="task_experiment",
        )


def test_work_request_draft_requires_expected_evidence() -> None:
    with pytest.raises(ValidationError, match="expected_evidence"):
        WorkRequestDraft(objective="Measure")


def test_scientific_turn_result_discriminates_all_statuses() -> None:
    adapter = TypeAdapter(ScientificTurnResult)

    cases = [
        ScientificWorkRequestResult(
            status="request_work",
            assessment=ScientificAssessment(statement="need more evidence"),
            work_request=WorkRequestDraft(
                objective="Run experiment",
                expected_evidence=["accuracy"],
            ),
            session=session_ref(),
        ),
        ScientificQuestionResult(
            status="needs_user_input",
            assessment=ScientificAssessment(statement="need a dataset"),
            question=QuestionDraft(text="Which dataset?", reason="missing"),
            session=session_ref(),
        ),
        ScientificCompletedResult(
            status="completed",
            opinion=ScientificOpinion(
                verdict=ScientificVerdict.INCONCLUSIVE,
                statement="cannot conclude yet",
            ),
            session=session_ref(status=SessionStatus.COMPLETED),
        ),
        ScientificFailedResult(
            status="failed",
            error=ModuleError(
                code=ErrorCode.TOOL_FAILED,
                message="loop failed",
                retryable=False,
            ),
        ),
    ]

    for case in cases:
        dumped = case.model_dump()
        validated = adapter.validate_python(dumped)
        assert validated.status == case.status


def test_scientific_turn_result_rejects_unknown_status() -> None:
    adapter = TypeAdapter(ScientificTurnResult)
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "status": "analyze",
                "opinion": {"verdict": "supports", "statement": "x"},
            }
        )


def test_supports_opinion_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="evidence"):
        ScientificOpinion(verdict=ScientificVerdict.SUPPORTS, statement="it works")


def test_inconclusive_opinion_allows_empty_evidence() -> None:
    opinion = ScientificOpinion(
        verdict=ScientificVerdict.INCONCLUSIVE,
        statement="not enough evidence",
    )
    assert opinion.evidence_artifact_ids == []


def test_scientific_turn_request_first_call_rejects_outcome() -> None:
    with pytest.raises(ValidationError, match="first call"):
        ScientificTurnRequest(
            run_id="run_example",
            research=research_request(),
            work_outcome=WorkOutcome(
                work_request_id="work_round1",
                workflow_revision=1,
                summary="done",
                tasks=[
                    WorkTaskOutcome(
                        task_id="task_experiment",
                        status="completed",
                        summary="ran",
                    )
                ],
            ),
            budget=TaskBudget(max_steps=10, max_llm_calls=10, timeout_seconds=60),
        )


def test_scientific_turn_request_resume_rejects_outcome_and_answers() -> None:
    with pytest.raises(ValidationError, match="cannot carry both"):
        ScientificTurnRequest(
            run_id="run_example",
            research=research_request(),
            work_outcome=WorkOutcome(
                work_request_id="work_round1",
                workflow_revision=1,
                summary="done",
                tasks=[
                    WorkTaskOutcome(
                        task_id="task_experiment",
                        status="completed",
                        summary="ran",
                    )
                ],
            ),
            answers=[
                UserAnswer(
                    question_id="question_x",
                    values={"dataset": "demo"},
                    answered_at=NOW,
                )
            ],
            parent_session_id="session_sci",
            budget=TaskBudget(max_steps=10, max_llm_calls=10, timeout_seconds=60),
        )


def _execution_artifact() -> ArtifactRef:
    return ArtifactRef(
        id="artifact_metrics",
        kind="experiment_result",
        producer=AgentOwner.EXPERIMENT,
        run_id="run_example",
        task_id="task_experiment",
        attempt_number=1,
        uri="file:///artifacts/metrics.json",
        sha256="0" * 64,
        media_type="application/json",
        summary="metrics",
    )


def test_artifact_ref_accepts_execution_provenance() -> None:
    artifact = _execution_artifact()
    assert artifact.task_id == "task_experiment"
    assert artifact.attempt_number == 1
    assert artifact.session_id is None


def test_artifact_ref_rejects_half_task_attempt() -> None:
    with pytest.raises(ValidationError, match="both task_id and attempt_number"):
        ArtifactRef(
            id="artifact_metrics",
            kind="experiment_result",
            producer=AgentOwner.EXPERIMENT,
            run_id="run_example",
            task_id="task_experiment",
            uri="file:///artifacts/metrics.json",
            sha256="0" * 64,
            media_type="application/json",
            summary="metrics",
        )


def test_artifact_ref_accepts_scientific_session_provenance() -> None:
    artifact = ArtifactRef(
        id="artifact_lit",
        kind="literature",
        producer=AgentOwner.SCIENTIFIC,
        run_id="run_example",
        session_id="session_sci",
        uri="file:///artifacts/lit.json",
        sha256="0" * 64,
        media_type="application/json",
        summary="literature results",
    )
    assert artifact.session_id == "session_sci"
    assert artifact.task_id is None


def test_artifact_ref_rejects_session_with_task() -> None:
    with pytest.raises(ValidationError, match="session-bound"):
        ArtifactRef(
            id="artifact_lit",
            kind="literature",
            producer=AgentOwner.SCIENTIFIC,
            run_id="run_example",
            session_id="session_sci",
            task_id="task_experiment",
            uri="file:///artifacts/lit.json",
            sha256="0" * 64,
            media_type="application/json",
            summary="literature results",
        )


def test_artifact_ref_accepts_orchestrator_import_provenance() -> None:
    artifact = ArtifactRef(
        id="artifact_imported",
        kind="input",
        producer=AgentOwner.ORCHESTRATOR,
        run_id="run_example",
        uri="file:///artifacts/input.json",
        sha256="0" * 64,
        media_type="application/json",
        summary="imported input",
        metadata={"source_type": "import"},
    )
    assert artifact.task_id is None
    assert artifact.session_id is None


def test_artifact_ref_rejects_orchestrator_without_source_type() -> None:
    with pytest.raises(ValidationError, match="source_type"):
        ArtifactRef(
            id="artifact_imported",
            kind="input",
            producer=AgentOwner.ORCHESTRATOR,
            run_id="run_example",
            uri="file:///artifacts/input.json",
            sha256="0" * 64,
            media_type="application/json",
            summary="imported input",
        )


def test_work_task_outcome_requires_error_for_failed() -> None:
    with pytest.raises(ValidationError, match="requires error"):
        WorkTaskOutcome(
            task_id="task_experiment",
            status="failed",
            summary="crashed",
        )


def test_work_outcome_rejects_duplicate_task_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate task"):
        WorkOutcome(
            work_request_id="work_round1",
            workflow_revision=1,
            summary="done",
            tasks=[
                WorkTaskOutcome(task_id="task_x", status="completed", summary="a"),
                WorkTaskOutcome(task_id="task_x", status="completed", summary="b"),
            ],
        )


def test_schema_1_1_object_is_rejected_as_2_0() -> None:
    """A schema 1.1 wire object cannot validate as the 2.0 contract."""
    with pytest.raises(ValidationError):
        WorkRequest.model_validate(
            {
                "schema_version": "1.1",
                "id": "work_round1",
                "run_id": "run_example",
                "scientific_session_id": "session_sci",
                "request": {
                    "objective": "Measure",
                    "expected_evidence": ["accuracy"],
                },
                "status": "requested",
                "created_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(),
            }
        )


def test_orchestrator_artifact_cannot_pose_as_execution() -> None:
    with pytest.raises(ValidationError, match="orchestrator artifact cannot"):
        ArtifactRef(
            id="artifact_x",
            kind="input",
            producer=AgentOwner.ORCHESTRATOR,
            run_id="run_example",
            task_id="task_x",
            attempt_number=1,
            uri="file:///artifacts/x.json",
            sha256="0" * 64,
            media_type="application/json",
            summary="imported input",
            metadata={"source_type": "import"},
        )


def test_scientific_turn_rejects_cross_run_artifact() -> None:
    artifact = ArtifactRef(
        id="artifact_other",
        kind="experiment_result",
        producer=AgentOwner.EXPERIMENT,
        run_id="run_other",
        task_id="task_x",
        attempt_number=1,
        uri="file:///artifacts/x.json",
        sha256="0" * 64,
        media_type="application/json",
        summary="evidence",
    )
    with pytest.raises(ValidationError, match="same run"):
        ScientificTurnRequest(
            run_id="run_example",
            research=research_request(),
            authorized_artifacts=[artifact],
            budget=TaskBudget(max_steps=5, max_llm_calls=5, timeout_seconds=60),
        )


def test_scientific_turn_rejects_duplicate_authorized_artifact() -> None:
    artifact = ArtifactRef(
        id="artifact_x",
        kind="experiment_result",
        producer=AgentOwner.EXPERIMENT,
        run_id="run_example",
        task_id="task_x",
        attempt_number=1,
        uri="file:///artifacts/x.json",
        sha256="0" * 64,
        media_type="application/json",
        summary="evidence",
    )
    with pytest.raises(ValidationError, match="unique"):
        ScientificTurnRequest(
            run_id="run_example",
            research=research_request(),
            authorized_artifacts=[artifact, artifact],
            budget=TaskBudget(max_steps=5, max_llm_calls=5, timeout_seconds=60),
        )


def test_module_result_completed_cannot_carry_request_work() -> None:
    with pytest.raises(ValidationError, match="completed result cannot"):
        ModuleResult(
            status=ModuleStatus.COMPLETED,
            summary="done",
            request_work={"assessment": {}},
        )


def test_module_result_request_work_requires_paused_session() -> None:
    with pytest.raises(ValidationError, match="paused session"):
        ModuleResult(
            status=ModuleStatus.REQUEST_WORK,
            summary="more work",
            request_work={"assessment": {"statement": "need"}, "work_request": {}},
        )
