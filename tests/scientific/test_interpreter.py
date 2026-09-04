"""Tests for the deterministic execution -> scientific work-brief interpreter."""

import hashlib
import json
from datetime import UTC, datetime

from resagent2_contracts import (
    AgentOwner,
    ArtifactRef,
    DatasetRef,
    ErrorCode,
    ModuleError,
    ResearchRequest,
    RunBudget,
    ScientificTurnRequest,
    TaskBudget,
    WarningRecord,
    WorkOutcome,
    WorkRequestDraft,
    WorkTaskOutcome,
)
from resagent2_runtime import AgentState
from resagent2_scientific.context import build_context
from resagent2_scientific.interpreter import render_work_brief


def _artifact(artifact_id: str, kind: str = "experiment_result") -> ArtifactRef:
    return ArtifactRef(
        id=artifact_id,
        kind=kind,
        producer=AgentOwner.EXPERIMENT,
        run_id="run_example",
        task_id="task_run_training",
        attempt_number=1,
        uri=f"file:///tmp/{artifact_id}",
        sha256=hashlib.sha256(artifact_id.encode()).hexdigest(),
        media_type="application/json",
        summary="evidence",
    )


def _completed(
    task_id: str = "task_run_training",
    *,
    summary: str = "ran training",
    artifact_ids: tuple[str, ...] = ("artifact_exp",),
    warnings: tuple[WarningRecord, ...] = (),
) -> WorkTaskOutcome:
    return WorkTaskOutcome(
        task_id=task_id,
        status="completed",
        summary=summary,
        artifact_ids=list(artifact_ids),
        warnings=list(warnings),
    )


def _failed(
    task_id: str = "task_run_training",
    *,
    message: str = "Experiment command failed with exit code 1",
    retryable: bool = False,
) -> WorkTaskOutcome:
    return WorkTaskOutcome(
        task_id=task_id,
        status="failed",
        summary="training failed",
        error=ModuleError(
            code=ErrorCode.TOOL_FAILED,
            message=message,
            retryable=retryable,
            details={
                "stderr_tail": "NameError: totla",
                "stderr_path": ".resagent2/experiment/commands/1.stderr.log",
                "stdout_path": ".resagent2/experiment/commands/1.stdout.log",
                "command": "python train.py",
                "exit_code": 1,
            },
        ),
    )


def _blocked(task_id: str = "task_analyze") -> WorkTaskOutcome:
    return WorkTaskOutcome(
        task_id=task_id,
        status="blocked",
        summary="blocked on dependency",
        error=ModuleError(
            code=ErrorCode.ARTIFACT_MISSING,
            message="dependency missing",
            retryable=True,
        ),
    )


def _draft() -> WorkRequestDraft:
    return WorkRequestDraft(
        objective="Compare SE and baseline", expected_evidence=["accuracy"]
    )


def _state(memory=None) -> AgentState:
    now = datetime.now(UTC)
    return AgentState(
        session_id="session_s",
        agent_name="scientific",
        owner=AgentOwner.SCIENTIFIC,
        run_id="run_s",
        created_at=now,
        updated_at=now,
        memory=memory or {},
    )


def test_completed_task_renders_purpose_and_authorized_evidence() -> None:
    outcome = WorkOutcome(
        work_request_id="work_1",
        workflow_revision=1,
        summary="execution stable",
        tasks=[_completed()],
    )
    brief = render_work_brief(
        work_outcome=outcome,
        previous_work_request=_draft(),
        unresolved_task_outcomes=[],
        authorized_artifacts=[_artifact("artifact_exp")],
    )

    assert brief["purpose"]["objective"] == "Compare SE and baseline"
    assert brief["purpose"]["expected_evidence"] == ["accuracy"]
    outcomes = brief["outcomes"]
    assert len(outcomes) == 1
    assert "task_id" not in outcomes[0]
    assert outcomes[0]["execution_status"] == "completed"
    assert outcomes[0]["narrative"] == "ran training"
    assert outcomes[0]["narrative_use"] == "explanatory_only"
    assert outcomes[0]["evidence"] == [
        {
            "artifact_id": "artifact_exp",
            "kind": "experiment_result",
            "use": "read_artifact_before_content_based_claims",
        }
    ]
    assert brief["blocking_items"] == []
    assert "acknowledgement_required_task_ids" not in brief


def test_completed_with_warning_exposes_caveat_content() -> None:
    outcome = WorkOutcome(
        work_request_id="work_1",
        workflow_revision=1,
        summary="execution stable",
        tasks=[
            _completed(
                warnings=[
                    WarningRecord(
                        code="delivery_not_met",
                        message="report.json missing",
                        details={"path": "should_not_leak"},
                    )
                ]
            )
        ],
    )
    brief = render_work_brief(
        work_outcome=outcome,
        previous_work_request=None,
        unresolved_task_outcomes=[],
        authorized_artifacts=[],
    )

    entry = brief["outcomes"][0]
    assert entry["execution_status"] == "completed_with_caveats"
    assert entry["caveats"] == [
        {"code": "delivery_not_met", "message": "report.json missing"}
    ]
    assert "details" not in entry["caveats"][0]
    assert "should_not_leak" not in json.dumps(entry["caveats"])


def test_failed_task_exposes_short_error_and_diagnostic_excerpt() -> None:
    failed = _failed(retryable=False)
    brief = render_work_brief(
        work_outcome=None,
        previous_work_request=None,
        unresolved_task_outcomes=[failed],
        authorized_artifacts=[],
    )

    item = brief["blocking_items"][0]
    assert "task_id" not in item
    assert item["status"] == "failed"
    assert item["error_code"] == "tool_failed"
    assert item["message"] == "Experiment command failed with exit code 1"
    assert item["retryable"] is False
    assert item["diagnostic_excerpt"] == "NameError: totla"
    assert item["diagnostic_use"] == "execution_diagnosis_only"

    # The whitelist projection must not leak raw execution-detail keys or text.
    serialized = json.dumps(item)
    assert "python train.py" not in serialized
    assert ".stderr.log" not in serialized
    assert ".stdout.log" not in serialized
    for forbidden in (
        "stderr_path",
        "stdout_path",
        "stderr_tail",
        "exit_code",
        "details",
    ):
        assert forbidden not in serialized


def test_diagnostic_excerpt_keeps_tail_not_head() -> None:
    # stderr_tail is the log's final chunk; the exception is at its end, so the
    # bounded excerpt must keep the tail, never the head.
    head_noise = "HEAD_MARKER\n" + ("WARNING: noisy log line\n" * 300)
    failed = WorkTaskOutcome(
        task_id="task_x",
        status="failed",
        summary="failed",
        error=ModuleError(
            code=ErrorCode.TOOL_FAILED,
            message="Experiment command failed with exit code 1",
            retryable=False,
            details={"stderr_tail": head_noise + "NameError: totla"},
        ),
    )
    brief = render_work_brief(
        work_outcome=None,
        previous_work_request=None,
        unresolved_task_outcomes=[failed],
        authorized_artifacts=[],
    )

    excerpt = brief["blocking_items"][0]["diagnostic_excerpt"]
    assert len(excerpt) <= 1000
    assert excerpt.endswith("NameError: totla")
    assert "HEAD_MARKER" not in excerpt


def test_blocked_task_enters_blocking_items() -> None:
    brief = render_work_brief(
        work_outcome=None,
        previous_work_request=None,
        unresolved_task_outcomes=[_blocked()],
        authorized_artifacts=[],
    )

    item = brief["blocking_items"][0]
    assert item["status"] == "blocked"
    assert item["error_code"] == "artifact_missing"
    assert item["retryable"] is True
    assert "diagnostic_excerpt" not in item


def test_unresolved_tasks_do_not_expose_internal_task_ids() -> None:
    brief = render_work_brief(
        work_outcome=None,
        previous_work_request=None,
        unresolved_task_outcomes=[_failed("task_a"), _blocked("task_b")],
        authorized_artifacts=[],
    )

    assert "task_id" not in brief["blocking_items"][0]
    assert "task_id" not in brief["blocking_items"][1]
    assert brief["purpose"] is None
    assert brief["outcomes"] == []


def test_unregistered_artifact_not_presented_as_evidence() -> None:
    outcome = WorkOutcome(
        work_request_id="work_1",
        workflow_revision=1,
        summary="execution stable",
        tasks=[_completed(artifact_ids=("artifact_not_authorized",))],
    )
    brief = render_work_brief(
        work_outcome=outcome,
        previous_work_request=None,
        unresolved_task_outcomes=[],
        authorized_artifacts=[],
    )

    entry = brief["outcomes"][0]
    assert entry["evidence"] == []
    assert entry["unregistered_artifact_ids"] == ["artifact_not_authorized"]


def test_render_is_pure_function() -> None:
    draft = _draft()
    outcome = WorkOutcome(
        work_request_id="work_1",
        workflow_revision=1,
        summary="execution stable",
        tasks=[_completed()],
    )
    unresolved = [_failed("task_a")]
    artifacts = [_artifact("artifact_exp")]

    def snapshot():
        return (
            draft.model_dump(mode="json"),
            outcome.model_dump(mode="json"),
            [task.model_dump(mode="json") for task in unresolved],
            [artifact.model_dump(mode="json") for artifact in artifacts],
        )

    before = snapshot()
    render_work_brief(
        work_outcome=outcome,
        previous_work_request=draft,
        unresolved_task_outcomes=unresolved,
        authorized_artifacts=artifacts,
    )
    assert before == snapshot()


def test_brief_does_not_leak_execution_fields() -> None:
    outcome = WorkOutcome(
        work_request_id="work_secret",
        workflow_revision=7,
        summary="execution stable",
        tasks=[_completed()],
    )
    brief = render_work_brief(
        work_outcome=outcome,
        previous_work_request=_draft(),
        unresolved_task_outcomes=[_failed("task_a")],
        authorized_artifacts=[_artifact("artifact_exp")],
    )

    assert set(brief.keys()) == {
        "purpose",
        "outcomes",
        "blocking_items",
    }
    serialized = json.dumps(brief)
    for forbidden in (
        "workflow_revision",
        "work_request_id",
        "work_secret",
        "workspace",
        "payload",
        "sha256",
        "uri",
        "media_type",
        "producer",
        "attempt_number",
        "run_id",
        "details",
        "stderr_path",
        "stdout_path",
        "stderr_tail",
        "exit_code",
        "python train.py",
    ):
        assert forbidden not in serialized


def test_failure_without_stderr_tail_has_no_diagnostic_excerpt() -> None:
    failed = WorkTaskOutcome(
        task_id="task_x",
        status="failed",
        summary="timed out",
        error=ModuleError(
            code=ErrorCode.TIMEOUT,
            message="experiment command timed out",
            retryable=True,
            details={"exit_code": 1, "timed_out": True},
        ),
    )
    brief = render_work_brief(
        work_outcome=None,
        previous_work_request=None,
        unresolved_task_outcomes=[failed],
        authorized_artifacts=[],
    )

    item = brief["blocking_items"][0]
    assert "diagnostic_excerpt" not in item
    assert "diagnostic_use" not in item


def test_build_context_emits_single_work_brief_section() -> None:
    turn = ScientificTurnRequest(
        run_id="run_example",
        research=ResearchRequest(
            goal="Evaluate the method",
            dataset_refs=[
                DatasetRef(dataset_id="cifar10", relative_path="cifar-10")
            ],
            budget=RunBudget(
                max_tasks=5,
                max_attempts_per_task=2,
                max_llm_calls=20,
                timeout_seconds=60,
            ),
        ),
        authorized_artifacts=[],
        work_outcome=WorkOutcome(
            work_request_id="work_1",
            workflow_revision=1,
            summary="execution stable",
            tasks=[_completed()],
        ),
        previous_work_request=_draft(),
        unresolved_task_outcomes=[],
        budget=TaskBudget(max_steps=10, max_llm_calls=10, timeout_seconds=60),
        parent_session_id="session_x",
    )
    sections = build_context(turn, _state())

    names = [section.name for section in sections]
    assert "dataset_catalog" in names
    assert "work_brief" in names
    assert "work_outcome" not in names
    assert "previous_work_request" not in names
    assert "unresolved_tasks" not in names
    dataset_section = next(
        section for section in sections if section.name == "dataset_catalog"
    )
    assert json.loads(dataset_section.content)["available_dataset_ids"] == ["cifar10"]
