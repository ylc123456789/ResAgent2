"""Deterministic handoffs preserve source reports, identities and history."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from resagent2_contracts import (
    AgentOwner, ArtifactCandidate, ArtifactRef, Attempt, ModuleError,
    ResearchIndex, ResearchIndexGroup, ResearchArtifactEntry, RecordedAnswer,
    WarningRecord, WorkAttemptRecord, WorkFeedback, WorkOutcome, WorkRecord,
    WorkRequest, WorkRequestDraft, WorkflowTask, WorkTaskOutcome,
)
from resagent2_orchestrator import ArtifactRegistry, DeterministicWorkInterpreter
from resagent2_orchestrator.interpreter import build_research_index
from resagent2_runtime.budget import execution_budget

NOW = datetime.now(UTC)


def work_record(*, report="Measured accuracy: 0.8. One experiment, no replication."):
    return WorkRecord(
        run_id="run_test", session_id="session_test", work_request_id="work_test",
        previous_work_request=WorkRequestDraft(
            objective="Compare methods", expected_evidence=["accuracy"], constraints=["Use the frozen split"],
        ),
        work_outcome=WorkOutcome(
            work_request_id="work_test", workflow_revision=1, summary="execution stable",
            tasks=[WorkTaskOutcome(task_id="task_measure", status="completed", summary="Fallback task text",
                                   artifact_ids=["artifact_metric"])],
        ),
        attempts=[WorkAttemptRecord(task_id="task_measure", attempt_number=1, status="completed",
                                    summary=report, artifact_ids=["artifact_metric"])],
    )


def test_report_preserves_original_content_without_mutation_or_model_usage():
    record = work_record(report="Original report.\nExact number: 0.80000.\nLimit: one seed.")
    before = record.model_dump()
    interpreter = DeterministicWorkInterpreter()
    with execution_budget(max_llm_calls=1, timeout_seconds=30) as budget:
        report = interpreter.interpret(record)
        assert budget.usage.used == 0
    assert report == interpreter.interpret(record)
    assert record.model_dump() == before
    assert record.attempts[0].summary in report
    assert record.previous_work_request.objective in report
    assert record.previous_work_request.constraints[0] in report
    assert "task_measure" in report and "artifact_metric" in report
    assert "Fallback task text" not in report


def test_latest_report_is_selected_by_attempt_number_and_history_remains_distinct():
    record = work_record(report="Current result with remaining limitations.")
    record.attempts[0].attempt_number = 3
    record.attempts.extend([
        WorkAttemptRecord(task_id="task_measure", attempt_number=2, status="failed",
                          summary="Do not repeat this older interpretation."),
        WorkAttemptRecord(task_id="task_measure", attempt_number=1, status="failed",
                          summary="Do not repeat this oldest interpretation."),
    ])
    record.work_outcome.tasks[0].warnings = [WarningRecord(code="historical_warning", message="Review precision.")]
    report = DeterministicWorkInterpreter().interpret(record)
    assert report.count(record.attempts[0].summary) == 1
    assert "Latest attempt: 3; status: completed" in report
    assert report.index("attempt 1: failed") < report.index("attempt 2: failed")
    assert "Do not repeat" not in report
    assert "accumulated across attempts" in report and "Review precision." in report
    assert "Task status: completed" in report


def test_task_order_and_original_ids_distinguish_identical_report_text():
    record = work_record(report="Same report")
    record.work_outcome.tasks.insert(0, WorkTaskOutcome(
        task_id="task_code", status="completed", summary="Unused", artifact_ids=["artifact_code"],
    ))
    record.attempts.append(WorkAttemptRecord(
        task_id="task_code", attempt_number=1, status="completed",
        summary="Same report", artifact_ids=["artifact_code"],
    ))
    report = DeterministicWorkInterpreter().interpret(record)
    assert report.index("Task: task_code") < report.index("Task: task_measure")
    assert report.index("artifact_code") < report.index("Task: task_measure")
    assert report.count("Same report") == 2
    assert report.count("Latest attempt: 1; status: completed") == 2


def test_missing_report_and_unexecuted_task_do_not_echo_instruction_as_agent_work():
    record = work_record(report="")
    record.work_outcome.tasks.append(WorkTaskOutcome(
        task_id="task_blocked", status="blocked", summary="Run a future experiment",
        error=ModuleError(code="contract_error", message="dependency did not complete", retryable=False),
    ))
    report = DeterministicWorkInterpreter().interpret(record)
    assert "No recorded report." in report and "Not executed; no Agent report." in report
    assert "Run a future experiment" not in report and "Fallback task text" not in report
    assert "dependency did not complete" in report


def test_recorded_report_does_not_override_actual_task_status():
    record = work_record(report="The experiment succeeded, according to the Agent.")
    error = ModuleError(code="tool_failed", message="A later operation failed", retryable=False)
    record.work_outcome.tasks[0].status = "failed"
    record.work_outcome.tasks[0].error = error
    record.attempts[0].status = "failed"
    record.attempts[0].error = error
    report = DeterministicWorkInterpreter().interpret(record)
    assert "Task status: failed" in report
    assert record.attempts[0].summary in report and error.message in report


@pytest.mark.parametrize("invalid", ["foreign_task", "duplicate_attempt", "foreign_output", "output_without_attempt"])
def test_report_refuses_inconsistent_source_bindings(invalid):
    record = work_record()
    if invalid == "foreign_task":
        record.attempts[0].task_id = "task_other"
    elif invalid == "duplicate_attempt":
        record.attempts.append(record.attempts[0].model_copy())
    elif invalid == "foreign_output":
        record.work_outcome.tasks[0].artifact_ids = ["artifact_other"]
    else:
        record.attempts = []
    with pytest.raises(ValueError):
        DeterministicWorkInterpreter().interpret(record)


def test_long_recorded_report_is_not_truncated_before_context_composition():
    original = "Start of original report.\n" + "Detailed material\n" * 2000 + "Final limitation."
    assert original in DeterministicWorkInterpreter().interpret(work_record(report=original))


def test_work_feedback_requires_report_and_rejects_old_brief():
    fields = dict(
        run_id="run_test", session_id="session_test", work_request_id="work_test",
        work_record_artifact_id="artifact_record", index_artifact_id="artifact_index",
    )
    with pytest.raises(ValidationError, match="report"):
        WorkFeedback(**fields, report="")
    with pytest.raises(ValidationError, match="brief"):
        WorkFeedback(**fields, report="Recorded task results", brief={"statements": []})
    feedback = WorkFeedback(**fields, report="Recorded task results")
    assert WorkFeedback.model_validate_json(feedback.model_dump_json()) == feedback


def source(tmp_path, *, kind="data", media_type="application/json", content='{"accuracy": 0.8}'):
    registry = ArtifactRegistry(tmp_path)
    ref = registry.register(ArtifactCandidate(kind=kind, path="metrics.json", media_type=media_type,
        summary="Measured accuracy", content=content), grant=None, producer=AgentOwner.EXPERIMENT,
        run_id="run_test", task_id="task_measure", attempt_number=1, index=1, existing_ids=set())
    draft = WorkRequestDraft(objective="Compare methods", expected_evidence=["accuracy"])
    record = WorkRecord(run_id="run_test", session_id="session_test", work_request_id="work_test",
        previous_work_request=draft, work_outcome=WorkOutcome(work_request_id="work_test", workflow_revision=1,
        summary="Stable", tasks=[WorkTaskOutcome(task_id="task_measure", status="completed", summary="Measured",
                                                  artifact_ids=[ref.id])]))
    record_ref = registry.register_system_artifact(ArtifactCandidate(kind="work_record", path="record.json",
        media_type="application/json", summary="Execution facts", content=record.model_dump_json()),
        run_id="run_test", session_id="session_test", source_type="controller_work_record")
    index = ResearchIndex(run_id="run_test", groups=[ResearchIndexGroup(key="work_test", title=draft.objective,
        artifacts=[ResearchArtifactEntry.from_ref(ref), ResearchArtifactEntry.from_ref(record_ref)])])
    return registry, ref, record_ref, index


def test_index_preserves_failed_attempt_when_retry_succeeds(tmp_path):
    registry, failed_ref, _, _ = source(tmp_path)
    success_ref = registry.register(ArtifactCandidate(kind="data", path="success.json", media_type="application/json",
        summary="Successful retry", content='{"accuracy": 0.9}'), grant=None, producer=AgentOwner.EXPERIMENT,
        run_id="run_test", task_id="task_measure", attempt_number=2, index=1, existing_ids={failed_ref.id})
    failure = ModuleError(code="tool_failed", message="First attempt failed", retryable=True)
    task = WorkflowTask(id="task_measure", work_request_id="work_test", workflow_agent_kind="experiment",
        instruction="Measure", status="completed", attempts=[
            Attempt(number=1, status="failed", started_at=NOW, finished_at=NOW, error=failure, artifact_ids=[failed_ref.id]),
            Attempt(number=2, status="completed", started_at=NOW, finished_at=NOW, artifact_ids=[success_ref.id]),
        ])
    work = WorkRequest(id="work_test", run_id="run_test", scientific_session_id="session_test",
        request=WorkRequestDraft(objective="Compare methods", expected_evidence=["accuracy"]), created_at=NOW, updated_at=NOW)
    index = build_research_index(run_id="run_test", artifacts=[failed_ref, success_ref], work_requests=[work], tasks=[task])
    entries = index.groups[0].artifacts
    assert [(entry.artifact_id, entry.execution_status) for entry in entries] == [
        (failed_ref.id, "failed"), (success_ref.id, "completed")]
    assert "uri" not in index.model_dump_json() and "sha256" not in index.model_dump_json()


def test_index_rejects_foreign_scope_and_missing_attempt_binding(tmp_path):
    _, ref, _, _ = source(tmp_path)
    with pytest.raises(ValueError, match="foreign Run"):
        build_research_index(run_id="run_other", artifacts=[ref], work_requests=[])
    with pytest.raises(ValueError, match="source task"):
        build_research_index(run_id="run_test", artifacts=[ref], work_requests=[])


def test_index_distinguishes_imported_materials_from_final_report():
    refs = [ArtifactRef(
        id=artifact_id, kind=kind, producer=AgentOwner.ORCHESTRATOR, run_id="run_test",
        uri="file:///unused/" + artifact_id, sha256="0" * 64,
        media_type="text/plain", summary=kind, metadata={"source_type": source_type},
    ) for artifact_id, kind, source_type in [
        ("artifact_input", "data", "import"),
        ("artifact_final_report", "final_report", "final_report"),
    ]]
    index = build_research_index(run_id="run_test", artifacts=refs, work_requests=[])
    assert {group.key: [entry.artifact_id for entry in group.artifacts]
            for group in index.groups} == {
                "inputs": ["artifact_input"], "scientific": ["artifact_final_report"],
            }


def answer_ref(registry, *, question_id="question_metric", task_id="task_measure", attempt_number=1,
               session_id=None, body_task_id=None):
    answer = RecordedAnswer(
        question_id=question_id, question_text="Which metric are these values, and which direction is better?",
        requested_fields=["metric", "direction"], values={"metric": "accuracy", "direction": "higher"},
        answered_at=NOW, run_id="run_test", task_id=body_task_id or task_id,
        attempt_number=attempt_number, session_id=session_id,
    )
    return registry.register_system_artifact(
        ArtifactCandidate(kind="answer", path="answer.json", media_type="application/json",
                          summary="Paired metric clarification", content=answer.model_dump_json()),
        run_id="run_test", task_id=task_id, attempt_number=attempt_number, session_id=session_id,
        source_type="controller_answer",
    )


def index_with_answer(tmp_path, **answer_options):
    registry, measured, record_ref, _ = source(tmp_path)
    answer = answer_ref(registry, **answer_options)
    task = WorkflowTask(id="task_measure", work_request_id="work_test", workflow_agent_kind="experiment",
        instruction="Measure", status="completed", attempts=[Attempt(
            number=1, status="completed", started_at=NOW, finished_at=NOW, artifact_ids=[measured.id],
        )])
    work = WorkRequest(id="work_test", run_id="run_test", scientific_session_id="session_test",
        request=WorkRequestDraft(objective="Compare methods", expected_evidence=["accuracy"]),
        created_at=NOW, updated_at=NOW)
    refs = [measured, record_ref, answer]
    return registry, refs, task, work, answer


def test_task_answer_is_indexed_and_readable_with_original_provenance(tmp_path):
    from resagent2_components import RegisteredArtifactReader

    _, refs, task, work, answer = index_with_answer(tmp_path)
    original_attempt_ids = list(task.attempts[0].artifact_ids)
    index = build_research_index(run_id="run_test", artifacts=refs, work_requests=[work], tasks=[task])
    assert {entry.artifact_id for entry in index.groups[0].artifacts} == {ref.id for ref in refs}
    assert index.groups[0].key == work.id
    # User input keeps its own provenance instead of masquerading as an Agent output.
    assert task.attempts[0].artifact_ids == original_attempt_ids and answer.id not in original_attempt_ids
    pair = json.loads(RegisteredArtifactReader(refs, run_id="run_test").read_text(answer.id)["content"])
    assert pair["question_text"].startswith("Which metric")
    assert pair["values"] == {"metric": "accuracy", "direction": "higher"}


@pytest.mark.parametrize("invalid", ["scope", "attempt", "hash", "missing"])
def test_index_rejects_invalid_answer_source(tmp_path, invalid):
    from urllib.parse import urlparse
    from urllib.request import url2pathname
    from pathlib import Path

    options = {"body_task_id": "task_other"} if invalid == "scope" else {}
    if invalid == "attempt":
        options["attempt_number"] = 2
    _, refs, task, work, answer = index_with_answer(tmp_path, **options)
    path = Path(url2pathname(urlparse(answer.uri).path))
    if invalid == "hash":
        path.write_text("{}")
    elif invalid == "missing":
        path.unlink()
    match = {"scope": "provenance", "attempt": "source attempt", "hash": "sha256", "missing": "missing"}[invalid]
    with pytest.raises(ValueError, match=match):
        build_research_index(run_id="run_test", artifacts=refs, work_requests=[work], tasks=[task])


def test_full_index_keeps_historical_answers_separate_from_current_work(tmp_path):
    registry, refs, task, work, answer = index_with_answer(tmp_path)
    historical = answer_ref(registry, question_id="question_earlier", task_id=None,
                            attempt_number=None, session_id="session_test")
    refs.append(historical)
    index = build_research_index(run_id="run_test", artifacts=refs, work_requests=[work], tasks=[task])
    assert {entry.artifact_id for group in index.groups for entry in group.artifacts} == {ref.id for ref in refs}
    assert next(group for group in index.groups if group.key == "scientific").artifacts[0].artifact_id == historical.id
