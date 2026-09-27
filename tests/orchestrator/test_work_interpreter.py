"""The reverse boundary preserves provenance, cited inputs and Run budgets."""
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from resagent2_contracts import (
    AgentOwner, ArtifactCandidate, ArtifactRef, Attempt, CitedStatement, ModuleError,
    ResearchIndex, ResearchIndexGroup, ResearchArtifactEntry, RecordedAnswer, WorkBrief, WorkFeedback,
    VerificationResult, WorkOutcome, WorkRecord, WorkRequest, WorkRequestDraft, WorkflowTask, WorkTaskOutcome,
)
from resagent2_orchestrator import ArtifactRegistry, LLMWorkInterpreter
from resagent2_orchestrator.interpreter import build_research_index
from resagent2_runtime.budget import BudgetExhaustedError, execution_budget

NOW = datetime.now(UTC)


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


def test_index_rejects_foreign_scope_and_missing_attempt_binding(tmp_path):
    _, ref, _, _ = source(tmp_path)
    with pytest.raises(ValueError, match="foreign Run"):
        build_research_index(run_id="run_other", artifacts=[ref], work_requests=[])
    with pytest.raises(ValueError, match="source task"):
        build_research_index(run_id="run_test", artifacts=[ref], work_requests=[])


def test_work_feedback_requires_citations_and_rejects_removed_delta():
    with pytest.raises(ValidationError):
        WorkBrief(statements=[dict(text="Unsupported", artifact_ids=[])])
    with pytest.raises(ValidationError, match="index_changes"):
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


def test_task_answer_is_indexed_readable_and_supplied_as_paired_brief_source(tmp_path):
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
    client = Client([WorkBrief(statements=[CitedStatement(
        text="The user identified accuracy and the higher-is-better direction.", artifact_ids=[answer.id],
    )])])
    with execution_budget(max_llm_calls=1, timeout_seconds=30):
        result = LLMWorkInterpreter(client).interpret(record_ref=refs[1], index=index, artifacts=refs)
    payload = json.loads(client.prompts[0].split("\n", 1)[1])
    supplied = next(window for window in payload["source_windows"] if window["artifact_id"] == answer.id)
    assert json.loads(supplied["content"]) == pair
    assert result.statements[0].artifact_ids == [answer.id]


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


def test_full_index_keeps_history_but_brief_only_receives_current_work_and_explicit_inputs(tmp_path):
    registry, refs, task, work, answer = index_with_answer(tmp_path)
    historical = answer_ref(registry, question_id="question_earlier", task_id=None,
                            attempt_number=None, session_id="session_test")
    refs.append(historical)
    index = build_research_index(run_id="run_test", artifacts=refs, work_requests=[work], tasks=[task])
    assert {entry.artifact_id for group in index.groups for entry in group.artifacts} == {ref.id for ref in refs}
    client = Client([brief(answer.id)])
    with execution_budget(max_llm_calls=1, timeout_seconds=30):
        LLMWorkInterpreter(client).interpret(record_ref=refs[1], index=index, artifacts=refs)
    payload = json.loads(client.prompts[0].split("\n", 1)[1])
    supplied = {window["artifact_id"] for window in payload["source_windows"]}
    assert answer.id in supplied and historical.id not in supplied
    assert {group["key"] for group in payload["research_index"]["groups"]} == {work.id}

def execution_source(tmp_path, commands):
    results = [
        VerificationResult(command=command, exit_code=code, timed_out=timed_out,
                           stdout_path=f"command_{i}.stdout", stderr_path=f"command_{i}.stderr",
                           duration_seconds=1.0).model_dump(mode="json")
        for i, (command, code, timed_out) in enumerate(commands, 1)
    ]
    return source(tmp_path, kind="execution_record", content=json.dumps({"results": results}))


def supplied_execution_window(ref, record_ref, index):
    client = Client([brief(ref.id)])
    with execution_budget(max_llm_calls=1, timeout_seconds=30):
        LLMWorkInterpreter(client).interpret(
            record_ref=record_ref, index=index, artifacts=[ref, record_ref],
        )
    payload = json.loads(client.prompts[0].split("\n", 1)[1])
    return next(window for window in payload["source_windows"] if window["artifact_id"] == ref.id)


def test_long_failed_commands_do_not_hide_later_successful_outcomes(tmp_path):
    commands = [
        (f"python stage_{i}.py", 0, False) for i in range(1, 6)
    ] + [
        ("python -c 'failed_lookup " + "x" * 20_000 + " calibration'", 1, False),
        ("python -c 'retry_lookup " + "y" * 20_000 + " calibration'", 1, False),
        ("python corrected_check.py", 0, False),
        ("python summarize.py", 0, False),
    ]
    registry, ref, record_ref, index = execution_source(tmp_path, commands)
    before = (registry.root / ref.run_id / ref.id / "metrics.json").read_bytes()
    window = supplied_execution_window(ref, record_ref, index)
    facts = json.loads(window["content"])
    assert facts["order"] == "oldest_to_newest"
    assert facts["total_records"] == 9 and facts["historical_failed_records"] == 2
    assert facts["omitted_earlier_records"] == 0
    assert [item["record_number"] for item in facts["results"]] == list(range(1, 10))
    assert [item["exit_code"] for item in facts["results"]] == [0] * 5 + [1, 1, 0, 0]
    assert facts["results"][5]["command_truncated"] is True
    assert facts["results"][-1]["command"] == "python summarize.py"
    assert facts["results"][-1]["stderr_path"] == "command_9.stderr"
    assert len(window["content"]) <= 12_000 and window["truncated"] is True
    assert (registry.root / ref.run_id / ref.id / "metrics.json").read_bytes() == before


def test_many_execution_records_keep_recent_whole_suffix_and_disclose_omissions(tmp_path):
    commands = [(f"python run_{i}.py " + "x" * 1500, 1 if i == 0 else 0, False)
                for i in range(100)]
    _, ref, record_ref, index = execution_source(tmp_path, commands)
    window = supplied_execution_window(ref, record_ref, index)
    facts = json.loads(window["content"])
    omitted = facts["omitted_earlier_records"]
    assert 0 < omitted < 100
    assert facts["historical_failed_records"] == 1  # Omitted early failure is not erased.
    assert [item["record_number"] for item in facts["results"]] == list(range(omitted + 1, 101))
    assert all(item["exit_code"] == 0 for item in facts["results"])
    assert len(window["content"]) <= 12_000


@pytest.mark.parametrize("commands", [[], [("python delayed.py", 0, True)]])
def test_execution_view_handles_empty_history_and_timeout(tmp_path, commands):
    _, ref, record_ref, index = execution_source(tmp_path, commands)
    facts = json.loads(supplied_execution_window(ref, record_ref, index)["content"])
    assert facts["total_records"] == len(commands)
    assert facts["omitted_earlier_records"] == 0
    assert facts["historical_failed_records"] == len(commands)


@pytest.mark.parametrize("invalid", [
    "not-json", "[]", "{}", '{"results": {}}', '{"results": [{"command": "python x.py"}]}',
])
def test_execution_view_rejects_invalid_record_before_model_dispatch(tmp_path, invalid):
    _, ref, record_ref, index = source(tmp_path, kind="execution_record", content=invalid)
    client = Client([brief(ref.id)])
    with execution_budget(max_llm_calls=1, timeout_seconds=30) as budget:
        with pytest.raises(ValueError):
            LLMWorkInterpreter(client).interpret(
                record_ref=record_ref, index=index, artifacts=[ref, record_ref],
            )
        assert budget.usage.used == 0
    assert client.prompts == []


def test_execution_view_verifies_hash_outside_selected_suffix(tmp_path):
    commands = [(f"python run_{i}.py " + "x" * 1500, 0, False) for i in range(100)]
    registry, ref, record_ref, index = execution_source(tmp_path, commands)
    before = json.loads(supplied_execution_window(ref, record_ref, index)["content"])
    assert before["omitted_earlier_records"] > 0
    path = registry.root / ref.run_id / ref.id / "metrics.json"
    path.write_text(path.read_text().replace("run_0.py", "other.py"))
    with pytest.raises(ValueError, match="sha256"):
        supplied_execution_window(ref, record_ref, index)
