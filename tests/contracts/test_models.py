from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from resagent2_contracts import (
    ArtifactCandidate,
    ArtifactRef,
    AskUserInput,
    Attempt,
    AttemptStatus,
    Capability,
    CodeUnderstandInput,
    ErrorCode,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    PendingQuestion,
    QuestionDraft,
    RunBudget,
    ScientificPlanInput,
    SuccessCriterion,
    TaskBudget,
    TaskProposal,
    TaskStatus,
    UserAnswer,
    VerificationMode,
    Workflow,
    WorkflowProposal,
    WorkflowTask,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSource,
    ModuleTaskRequest,
    ResearchRequest,
    WarningRecord,
)


NOW = datetime(2026, 8, 26, tzinfo=UTC)


def criterion() -> SuccessCriterion:
    return SuccessCriterion(
        description="Produce a validated plan",
        verification=VerificationMode.AUTOMATIC,
        evidence_key="workflow",
    )


def research_request() -> ResearchRequest:
    return ResearchRequest(
        goal="Evaluate the proposed method",
        context="A small reference implementation is available.",
        budget=RunBudget(
            max_tasks=8,
            max_attempts_per_task=2,
            max_llm_calls=20,
            timeout_seconds=3600,
        ),
    )


def plan_input() -> ScientificPlanInput:
    return ScientificPlanInput(request=research_request())


def task(task_id: str, depends_on: list[str] | None = None) -> WorkflowTask:
    return WorkflowTask(
        id=task_id,
        capability=Capability.CODE_UNDERSTAND,
        goal="Inspect the entry point",
        inputs=CodeUnderstandInput(question="Where is the entry point?"),
        depends_on=depends_on or [],
        success_criteria=[criterion()],
    )


def module_error() -> ModuleError:
    return ModuleError(
        code=ErrorCode.TOOL_FAILED,
        message="Tool exited with a non-zero status",
        retryable=True,
    )


def test_schema_round_trip_preserves_contract() -> None:
    workflow = Workflow(
        run_id="run_example",
        revision=1,
        tasks=[task("task_plan")],
        created_from="initial scientific proposal",
    )

    restored = Workflow.model_validate_json(workflow.model_dump_json())

    assert restored == workflow
    assert restored.schema_version == "1.0"


@pytest.mark.parametrize(
    ("field", "bad_id"),
    [
        ("run_id", "task_example"),
        ("task_id", "run_example"),
        ("question_id", "artifact_example"),
    ],
)
def test_id_namespaces_are_not_interchangeable(field: str, bad_id: str) -> None:
    if field == "run_id":
        with pytest.raises(ValidationError):
            Workflow(run_id=bad_id, revision=1, tasks=[], created_from="test")
    elif field == "task_id":
        with pytest.raises(ValidationError):
            task(bad_id)
    else:
        with pytest.raises(ValidationError):
            UserAnswer(question_id=bad_id, values={"choice": "yes"}, answered_at=NOW)


def test_workflow_rejects_unknown_dependency() -> None:
    with pytest.raises(ValidationError, match="unknown task"):
        Workflow(
            run_id="run_example",
            revision=1,
            tasks=[task("task_analyze", ["task_missing"])],
            created_from="test",
        )


def test_workflow_rejects_dependency_cycle() -> None:
    with pytest.raises(ValidationError, match="cycle"):
        Workflow(
            run_id="run_example",
            revision=1,
            tasks=[
                task("task_first", ["task_second"]),
                task("task_second", ["task_first"]),
            ],
            created_from="test",
        )


def test_proposal_rejects_duplicate_task_ids() -> None:
    proposal_task = TaskProposal(
        id="task_plan",
        capability=Capability.CODE_UNDERSTAND,
        goal="Inspect the entry point",
        rationale="Locate the code to change.",
        inputs=CodeUnderstandInput(question="Where is the entry point?"),
        success_criteria=[criterion()],
    )

    with pytest.raises(ValidationError, match="duplicate task"):
        WorkflowProposal(
            summary="Plan",
            tasks=[proposal_task, proposal_task],
            scientific_rationale="A plan is required before execution.",
        )


def test_task_input_must_match_capability() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        WorkflowTask(
            id="task_plan",
            capability=Capability.CODE_MODIFY,
            goal="Modify",
            inputs=CodeUnderstandInput(question="Where is the entry point?"),
            success_criteria=[criterion()],
        )


def test_module_request_input_must_match_capability() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        ModuleTaskRequest(
            run_id="run_example",
            task_id="task_plan",
            attempt_number=1,
            capability=Capability.CODE_MODIFY,
            goal="Modify",
            inputs=CodeUnderstandInput(question="Where is the entry point?"),
            budget=TaskBudget(max_steps=5, max_llm_calls=5, timeout_seconds=300),
        )


@pytest.mark.parametrize(
    "capability",
    [Capability.SCIENTIFIC_PLAN, Capability.ASK_USER],
)
def test_task_rejects_control_plane_capabilities(capability: Capability) -> None:
    if capability == Capability.SCIENTIFIC_PLAN:
        inputs = plan_input()
    else:
        inputs = AskUserInput(
            question=QuestionDraft(
                text="Which one?",
                requested_fields=["choice"],
                reason="need input",
            )
        )
    with pytest.raises(ValidationError, match="control-plane"):
        TaskProposal(
            id="task_control",
            capability=capability,
            goal="Control-plane",
            rationale="Control-plane operations do not belong in a workflow",
            inputs=inputs,
            success_criteria=[criterion()],
        )
    with pytest.raises(ValidationError, match="control-plane"):
        WorkflowTask(
            id="task_control",
            capability=capability,
            goal="Control-plane",
            inputs=inputs,
            success_criteria=[criterion()],
        )


def test_needs_user_input_requires_question() -> None:
    with pytest.raises(ValidationError, match="question"):
        ModuleResult[dict[str, str]](
            status=ModuleStatus.NEEDS_USER_INPUT,
            summary="A decision is required",
        )


@pytest.mark.parametrize("status", [ModuleStatus.FAILED, ModuleStatus.BLOCKED])
def test_failed_or_blocked_result_requires_error(status: ModuleStatus) -> None:
    with pytest.raises(ValidationError, match="error"):
        ModuleResult[dict[str, str]](status=status, summary="Could not continue")


def test_completed_result_rejects_error_and_question() -> None:
    with pytest.raises(ValidationError, match="completed"):
        ModuleResult[dict[str, str]](
            status=ModuleStatus.COMPLETED,
            summary="Done",
            error=module_error(),
            question=QuestionDraft(text="Continue?", reason="Unexpected branch"),
        )


def test_warning_status_and_warning_records_cannot_disagree() -> None:
    warning = WarningRecord(code="unverified", message="One metric was not verified")

    with pytest.raises(ValidationError, match="warnings"):
        ModuleResult[dict[str, str]](
            status=ModuleStatus.COMPLETED_WITH_WARNINGS,
            summary="Done with a limitation",
        )

    with pytest.raises(ValidationError, match="warnings"):
        ModuleResult[dict[str, str]](
            status=ModuleStatus.COMPLETED,
            summary="Done",
            warnings=[warning],
        )


def test_artifact_ref_requires_complete_provenance() -> None:
    with pytest.raises(ValidationError):
        ArtifactRef(
            id="artifact_metrics",
            kind="experiment_result",
            producer="experiment",
            run_id="run_example",
            task_id="task_experiment",
            attempt_number=1,
            uri="artifacts/run_example/metrics.json",
            media_type="application/json",
            summary="Evaluation metrics",
        )


def test_artifact_candidate_is_not_a_registered_artifact() -> None:
    candidate = ArtifactCandidate(
        kind="experiment_result",
        path="outputs/metrics.json",
        media_type="application/json",
        summary="Unregistered metrics",
    )

    dumped = candidate.model_dump()
    assert "id" not in dumped
    assert "run_id" not in dumped
    assert "sha256" not in dumped


def test_attempt_rejects_illegal_terminal_combinations() -> None:
    with pytest.raises(ValidationError, match="finished_at"):
        Attempt(number=1, status=AttemptStatus.COMPLETED, started_at=NOW)

    with pytest.raises(ValidationError, match="error"):
        Attempt(
            number=1,
            status=AttemptStatus.FAILED,
            started_at=NOW,
            finished_at=NOW,
        )


def test_attempt_payload_round_trips() -> None:
    attempt = Attempt(
        number=1,
        status=AttemptStatus.COMPLETED,
        started_at=NOW,
        finished_at=NOW,
        payload={"accuracy": 0.9},
    )
    restored = Attempt.model_validate_json(attempt.model_dump_json())
    assert restored.payload == {"accuracy": 0.9}


def test_question_and_answer_have_distinct_owners() -> None:
    pending = PendingQuestion(
        id="question_dataset",
        run_id="run_example",
        task_id="task_plan",
        text="Which dataset should be used?",
        requested_fields=["dataset"],
        created_at=NOW,
    )
    answer = UserAnswer(
        question_id=pending.id,
        values={"dataset": "demo"},
        answered_at=NOW,
    )

    assert pending.id == answer.question_id


def test_workspace_grant_rejects_paths_outside_root() -> None:
    with pytest.raises(ValidationError, match="relative"):
        WorkspaceGrant(
            root="/work/repo",
            mode=WorkspaceMode.READ_ONLY,
            allowed_paths=["/etc/passwd"],
            source=WorkspaceSource.EXISTING,
        )


def test_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RunBudget(
            max_tasks=8,
            max_attempts_per_task=2,
            max_llm_calls=20,
            timeout_seconds=3600,
            undocumented_switch=True,
        )
