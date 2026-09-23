"""The reverse boundary preserves provenance, cited inputs and Run budgets."""
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from resagent2_contracts import (
    AgentOwner, ArtifactCandidate, ArtifactRef, Attempt, CitedStatement, ModuleError,
    ResearchIndex, ResearchIndexGroup, ResearchArtifactEntry, WorkBrief, WorkFeedback,
    WorkOutcome, WorkRecord, WorkRequest, WorkRequestDraft, WorkflowTask, WorkTaskOutcome,
)
from resagent2_orchestrator import ArtifactRegistry, LLMWorkInterpreter
from resagent2_orchestrator.interpreter import build_research_index, research_index_changes
from resagent2_runtime.budget import BudgetExhaustedError, execution_budget

NOW = datetime.now(UTC)


def source(tmp_path, *, media_type="application/json", content='{"accuracy": 0.8}'):
    registry = ArtifactRegistry(tmp_path)
    ref = registry.register(ArtifactCandidate(kind="data", path="metrics.json", media_type=media_type,
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


class Client:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.prompts = []

    def next_action(self, prompt, action_type):
        self.prompts.append(prompt)
        return next(self.replies)


def brief(artifact_id):
    return WorkBrief(statements=[CitedStatement(text="Accuracy is 0.8", artifact_ids=[artifact_id])])


def test_interpreter_reads_real_frozen_content_and_corrects_unsupplied_citation(tmp_path):
    _, ref, record_ref, index = source(tmp_path)
    client = Client([brief("artifact_invented"), brief(ref.id)])
    with execution_budget(max_llm_calls=2, timeout_seconds=30) as budget:
        result = LLMWorkInterpreter(client).interpret(record_ref=record_ref, index=index, artifacts=[ref, record_ref])
        assert budget.usage.used == 2
    assert result.statements[0].artifact_ids == [ref.id]
    assert 'accuracy' in client.prompts[0] and '0.8' in client.prompts[0]
    assert 'Previous brief rejected' in client.prompts[1]


def test_interpreter_refuses_unread_binary_citation(tmp_path):
    _, ref, record_ref, index = source(tmp_path, media_type="application/octet-stream")
    client = Client([brief(ref.id), brief(ref.id)])
    with execution_budget(max_llm_calls=2, timeout_seconds=30):
        with pytest.raises(ValueError, match="unread artifact"):
            LLMWorkInterpreter(client).interpret(record_ref=record_ref, index=index, artifacts=[ref, record_ref])
    assert json.loads(client.prompts[0].split("\n", 1)[1])["source_windows"] == []


def test_interpreter_checks_hash_before_model_dispatch(tmp_path):
    registry, ref, record_ref, index = source(tmp_path)
    (registry.root / ref.run_id / ref.id / "metrics.json").write_text('{"accuracy": 99}')
    client = Client([brief(ref.id)])
    with execution_budget(max_llm_calls=2, timeout_seconds=30) as budget:
        with pytest.raises(ValueError, match="sha256"):
            LLMWorkInterpreter(client).interpret(record_ref=record_ref, index=index, artifacts=[ref, record_ref])
        assert budget.usage.used == 0
    assert client.prompts == []


def test_correction_cannot_open_another_budget(tmp_path):
    _, ref, record_ref, index = source(tmp_path)
    client = Client([brief("artifact_invented"), brief(ref.id)])
    with execution_budget(max_llm_calls=1, timeout_seconds=30) as budget:
        with pytest.raises(BudgetExhaustedError):
            LLMWorkInterpreter(client).interpret(record_ref=record_ref, index=index, artifacts=[ref, record_ref])
        assert budget.usage.used == 1
    assert len(client.prompts) == 1


def test_text_windows_keep_truncation_visible(tmp_path):
    _, ref, record_ref, index = source(tmp_path, media_type="text/plain", content="x" * 14000)
    client = Client([brief(record_ref.id)])
    with execution_budget(max_llm_calls=1, timeout_seconds=30):
        LLMWorkInterpreter(client).interpret(record_ref=record_ref, index=index, artifacts=[ref, record_ref])
    payload = json.loads(client.prompts[0].split("\n", 1)[1])
    window = next(item for item in payload["source_windows"] if item["artifact_id"] == ref.id)
    assert window["truncated"] is True
    assert len(window["content"]) <= 12000


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
    previous = index.model_copy(deep=True)
    previous.groups[0].artifacts = [entries[0]]
    delta = research_index_changes(previous, index)
    assert delta.groups[0].artifacts == [entries[1]]
    assert research_index_changes(index, index).groups == []
    assert previous.groups[0].artifacts == [entries[0]]


def test_index_rejects_foreign_scope_and_missing_attempt_binding(tmp_path):
    _, ref, _, _ = source(tmp_path)
    with pytest.raises(ValueError, match="foreign Run"):
        build_research_index(run_id="run_other", artifacts=[ref], work_requests=[])
    with pytest.raises(ValueError, match="source task"):
        build_research_index(run_id="run_test", artifacts=[ref], work_requests=[])


def test_work_feedback_requires_citations_and_same_run():
    with pytest.raises(ValidationError):
        WorkBrief(statements=[dict(text="Unsupported", artifact_ids=[])])
    with pytest.raises(ValidationError, match="another Run"):
        WorkFeedback(run_id="run_test", session_id="session_test", work_request_id="work_test",
            work_record_artifact_id="artifact_record", index_artifact_id="artifact_index",
            index_changes=ResearchIndex(run_id="run_other"), brief=brief("artifact_record"))


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
