"""Scientific consumes cited research handoffs without observing their original sources."""

import hashlib
import json
from datetime import UTC, datetime

import pytest

from resagent2_capabilities import ReadArtifactTool
from resagent2_components import ArtifactReadError, RegisteredArtifactReader, read_request_material
from resagent2_contracts import (
    AgentOwner, AgentPermissions, AgentRequest, ArtifactRef, CitedStatement,
    ErrorCode, ModuleError, ResearchArtifactEntry, ResearchIndex, ResearchIndexGroup,
    TaskBudget, WorkBrief, WorkFeedback, WorkOutcome, WorkRecord, WorkRequestDraft,
    WorkTaskOutcome,
)
from resagent2_runtime import AgentState, FinishCandidate, ScriptedLLMClient
from resagent2_scientific import ScientificAgent
from resagent2_scientific.completion import ScientificCompletionCheck, _observed_artifact_ids
from resagent2_scientific.context import build_context


RUN = "run_handoff"
SESSION = "session_handoff"


def freeze(root, artifact_id, kind, value, *, session=None):
    path = root / f"{artifact_id}.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    sources = {
        "work_record": "controller_work_record", "work_feedback": "controller_feedback",
        "research_index": "research_index", "answer": "controller_answer",
    }
    return ArtifactRef(
        id=artifact_id, kind=kind, run_id=RUN, producer=AgentOwner.ORCHESTRATOR,
        session_id=session, uri=path.as_uri(), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        media_type="application/json", summary=f"{kind} material",
        metadata={"source_type": sources.get(kind, "import")},
    )


def state():
    now = datetime.now(UTC)
    return AgentState(
        session_id=SESSION, agent_name="scientific", owner=AgentOwner.SCIENTIFIC,
        run_id=RUN, created_at=now, updated_at=now,
    )


def request(refs=(), *, parent=None, resume=()):
    return AgentRequest(
        run_id=RUN, agent=AgentOwner.SCIENTIFIC, instruction="Compare the measured results",
        input_artifacts=list(refs), parent_session_id=parent, resume_artifact_ids=list(resume),
        budget=TaskBudget(max_llm_calls=10, timeout_seconds=60),
        permissions=AgentPermissions(request_work=True),
    )


def handoff(root, *, session=SESSION, unresolved=()):
    data = freeze(root, "artifact_data", "experiment_result", {"accuracy": 0.8})
    record = WorkRecord(
        run_id=RUN, work_request_id="work_measure", session_id=session,
        previous_work_request=WorkRequestDraft(objective="Measure accuracy", expected_evidence=["accuracy"]),
        work_outcome=WorkOutcome(
            work_request_id="work_measure", workflow_revision=2, summary="Work finished",
            tasks=[WorkTaskOutcome(
                task_id="task_private", status="completed", summary="Measurement completed",
                artifact_ids=[data.id],
            )],
        ),
        unresolved_task_outcomes=list(unresolved),
    )
    record_ref = freeze(root, "artifact_record", "work_record", record.model_dump(mode="json"), session=session)
    index = ResearchIndex(run_id=RUN, groups=[ResearchIndexGroup(
        key="work_measure", title="Measure accuracy",
        artifacts=[ResearchArtifactEntry.from_ref(ref) for ref in (data, record_ref)],
    )])
    index_ref = freeze(root, "artifact_index", "research_index", index.model_dump(mode="json"))
    feedback = WorkFeedback(
        run_id=RUN, work_request_id="work_measure", session_id=session,
        work_record_artifact_id=record_ref.id, index_artifact_id=index_ref.id,
        brief=WorkBrief(statements=[CitedStatement(
            text="The measurement is available, with the recorded limitations.",
            artifact_ids=[data.id, record_ref.id],
        )]),
    )
    feedback_ref = freeze(root, "artifact_feedback", "work_feedback", feedback.model_dump(mode="json"), session=session)
    return [data, record_ref, index_ref, feedback_ref]


def with_indexed_answer(root, refs, answer):
    index = ResearchIndex.model_validate_json((root / "artifact_index.json").read_text())
    index.groups.append(ResearchIndexGroup(
        key="scientific", title="Scientific decisions",
        artifacts=[ResearchArtifactEntry.from_ref(answer)],
    ))
    latest = freeze(root, "artifact_answer_index", "research_index", index.model_dump(mode="json"))
    return [*refs, answer, latest]


def sections(turn):
    return {section.name: section.content for section in build_context(turn, state())}


def finish(evidence=(), limitations=()):
    return {"tool": "finish", "arguments": {
        "report": "Analysis complete",
        "artifacts": [{
            "kind": "scientific_opinion", "path": "opinion.json", "media_type": "application/json",
            "summary": "Analysis", "content": json.dumps({
                "verdict": "inconclusive", "statement": "Available evidence has limits",
                "evidence_artifact_ids": list(evidence), "limitations": list(limitations),
            }),
        }],
    }}


def test_first_invocation_presents_directory_not_flat_authorization(tmp_path):
    refs = handoff(tmp_path)
    initial = request(refs[:3])
    rendered = sections(initial)
    material = json.loads(rendered["research_materials"])
    assert material["index_artifact_id"] == refs[2].id
    assert material["index"]["groups"][0]["title"] == "Measure accuracy"
    assert "input_artifacts" not in rendered
    assert all(ref.uri not in rendered["research_materials"] for ref in refs)
    assert all(ref.sha256 not in rendered["research_materials"] for ref in refs)
    assert "work_brief" not in rendered
    assert "task_private" not in json.dumps(rendered)
    assert "workflow_revision" not in json.dumps(rendered)


def test_direct_materials_use_same_research_index_shape(tmp_path):
    data = freeze(tmp_path, "artifact_input", "data", {"value": 2})
    rendered = sections(request([data]))
    material = json.loads(rendered["research_materials"])
    assert material["index_artifact_id"] is None
    index = ResearchIndex.model_validate(material["index"])
    assert index.groups[0].key == "inputs"
    assert index.groups[0].artifacts[0].artifact_id == data.id


def test_feedback_presents_full_directory_once_and_current_cited_brief(tmp_path):
    refs = handoff(tmp_path)
    rendered = sections(request(refs, parent=SESSION, resume=[refs[-1].id]))
    navigation = json.loads(rendered["research_materials"])
    assert navigation["index_artifact_id"] == refs[2].id
    assert {entry["artifact_id"] for group in navigation["index"]["groups"]
            for entry in group["artifacts"]} == {refs[0].id, refs[1].id}
    material = json.loads(rendered[f"material_{refs[-1].id}"])["content"]
    assert set(material) == {"index_artifact_id", "work_record_artifact_id", "brief"}
    assert material["brief"]["statements"][0]["artifact_ids"] == [refs[0].id, refs[1].id]
    assert "work_outcome" not in material
    assert "task_private" not in json.dumps(rendered)
    assert "workflow_revision" not in json.dumps(rendered)


def test_answer_resume_keeps_full_directory_without_repeating_previous_brief(tmp_path):
    refs = handoff(tmp_path)
    answer = freeze(tmp_path, "artifact_answer", "answer", {
        "run_id": RUN, "session_id": SESSION, "question_id": "question_metric",
        "question_text": "Which metric matters?", "requested_fields": ["metric"],
        "values": {"metric": "accuracy"}, "answered_at": datetime.now(UTC).isoformat(),
    }, session=SESSION)
    rendered = sections(request(with_indexed_answer(tmp_path, refs, answer), parent=SESSION, resume=[answer.id]))
    assert f"material_{refs[-1].id}" not in rendered
    directory = json.loads(rendered["research_materials"])["index"]
    assert directory["groups"][0]["title"] == "Measure accuracy"
    assert "Which metric matters?" in rendered[f"material_{answer.id}"]
    assert "accuracy" in rendered[f"material_{answer.id}"]


def test_directory_read_observes_only_directory(tmp_path):
    refs = handoff(tmp_path)
    current = state()
    turn = request(refs, parent=SESSION, resume=[refs[-1].id])
    before = current.model_dump_json()
    build_context(turn, current)
    assert current.model_dump_json() == before
    reader = RegisteredArtifactReader(refs, run_id=RUN)
    tool = ReadArtifactTool(reader)
    observation = tool.execute(current, tool.input_model(artifact_id=refs[2].id))
    current.memory.update(observation.memory_updates)
    assert _observed_artifact_ids(current) == [refs[2].id]


def test_directory_citation_cannot_satisfy_original_evidence_requirement(tmp_path):
    refs = handoff(tmp_path)
    current = state()
    reader = RegisteredArtifactReader(refs, run_id=RUN)
    tool = ReadArtifactTool(reader)
    observed = tool.execute(current, tool.input_model(artifact_id=refs[2].id))
    current.memory.update(observed.memory_updates)
    candidate = FinishCandidate.model_validate(finish([refs[2].id])["arguments"])
    decision = ScientificCompletionCheck(
        [], ["experiment_result"], resolve_artifact=reader.resolve_ref, reader=reader,
    ).evaluate(current, candidate)
    assert not decision.complete
    assert "experiment_result" in decision.report


def test_listing_sources_does_not_allow_citation_before_actual_read(tmp_path):
    refs = handoff(tmp_path)
    client = ScriptedLLMClient([
        {"tool": "read_artifact", "arguments": {"artifact_id": refs[2].id}},
        finish([refs[0].id]),
        {"tool": "read_artifact", "arguments": {"artifact_id": refs[0].id}},
        finish([refs[0].id]),
    ])
    result = ScientificAgent(client).invoke(request(refs[:3]))
    assert result.status == "completed"
    assert "Cite only observed evidence" in client.contexts[2].text
    trace = json.loads(next(item.content for item in result.artifacts if item.kind == "observation_trace"))
    assert trace["observed_artifact_ids"] == [refs[2].id, refs[0].id]


@pytest.mark.parametrize("mismatch", ["run", "session", "hash"])
def test_feedback_material_keeps_scope_and_hash_checks(tmp_path, mismatch):
    refs = handoff(tmp_path)
    feedback = refs[-1]
    if mismatch == "hash":
        feedback = feedback.model_copy(update={"sha256": "0" * 64})
    else:
        original = json.loads((tmp_path / "artifact_feedback.json").read_text())
        if mismatch == "run":
            original["run_id"] = "run_other"
        else:
            original["session_id"] = "session_other"
        feedback = freeze(tmp_path, feedback.id, "work_feedback", original, session=SESSION)
    turn = request([*refs[:-1], feedback], parent=SESSION, resume=[feedback.id])
    with pytest.raises(ArtifactReadError):
        read_request_material(turn, feedback)


def test_latest_work_record_still_controls_completion_after_answer(tmp_path):
    ask = {"tool": "ask_user", "arguments": {
        "assessment": {"statement": "Need a metric choice"},
        "text": "Which metric?", "requested_fields": ["metric"],
    }}
    work = {"tool": "request_work", "arguments": {
        "assessment": {"statement": "Need measurements"},
        "work_request": {"objective": "Measure accuracy", "expected_evidence": ["accuracy"]},
    }}
    client = ScriptedLLMClient([work, ask, finish(), finish(limitations=["The requested measurement failed."])])
    agent = ScientificAgent(client)
    first = agent.invoke(request())
    failed = WorkTaskOutcome(
        task_id="task_failed", status="failed", summary="Measurement failed",
        error=ModuleError(code=ErrorCode.TOOL_FAILED, message="Crashed", retryable=False),
    )
    refs = handoff(tmp_path, session=first.session.id, unresolved=[failed])
    paused = agent.invoke(request(refs, parent=first.session.id, resume=[refs[-1].id]))
    assert paused.status == "needs_user_input"
    answer = freeze(tmp_path, "artifact_answer", "answer", {
        "run_id": RUN, "session_id": first.session.id, "question_id": "question_metric",
        "question_text": "Which metric?", "requested_fields": ["metric"],
        "values": {"metric": "accuracy"}, "answered_at": datetime.now(UTC).isoformat(),
    }, session=first.session.id)
    result = agent.invoke(request(with_indexed_answer(tmp_path, refs, answer), parent=first.session.id, resume=[answer.id]))
    assert result.status == "completed"
    assert "State at least one limitation" in client.contexts[-1].text
    assert "material_artifact_feedback" not in client.contexts[-1].included_sections


@pytest.mark.parametrize("invalid", ["missing", "hash", "scope"])
def test_invalid_raw_record_stops_before_model_invocation(tmp_path, invalid):
    refs = handoff(tmp_path)
    if invalid == "missing":
        refs.pop(1)
    elif invalid == "hash":
        refs[1] = refs[1].model_copy(update={"sha256": "0" * 64})
    else:
        record = json.loads((tmp_path / "artifact_record.json").read_text())
        record["work_request_id"] = "work_other"
        record["work_outcome"]["work_request_id"] = "work_other"
        refs[1] = freeze(tmp_path, "artifact_record", "work_record", record, session=SESSION)
    client = ScriptedLLMClient([finish()])
    result = ScientificAgent(client).invoke(request(refs, parent=SESSION, resume=[refs[-1].id]))
    assert result.status == "failed"
    assert result.error.code == ErrorCode.INVALID_INPUT
    assert client.contexts == []


def test_resumed_handoff_shows_full_history_and_only_current_brief(tmp_path):
    refs = handoff(tmp_path)
    previous_data = freeze(tmp_path, "artifact_previous_data", "experiment_result", {"accuracy": 0.7})
    previous_index = ResearchIndex(run_id=RUN, groups=[ResearchIndexGroup(
        key="work_baseline", title="Measure the baseline",
        artifacts=[ResearchArtifactEntry.from_ref(previous_data)],
    )])
    historical = freeze(tmp_path, "artifact_previous_index", "research_index", previous_index.model_dump(mode="json"))
    current_index = ResearchIndex.model_validate_json((tmp_path / "artifact_index.json").read_text())
    full_index = ResearchIndex(run_id=RUN, groups=[*previous_index.groups, *current_index.groups])
    latest = freeze(tmp_path, "artifact_full_index", "research_index", full_index.model_dump(mode="json"))
    feedback = WorkFeedback.model_validate_json((tmp_path / "artifact_feedback.json").read_text())
    feedback.index_artifact_id = latest.id
    feedback_ref = freeze(tmp_path, "artifact_feedback", "work_feedback", feedback.model_dump(mode="json"), session=SESSION)
    turn = request(
        [previous_data, historical, *refs[:-1], feedback_ref, latest],
        parent=SESSION, resume=[feedback_ref.id],
    )
    current = state()
    rendered = {section.name: section.content for section in build_context(turn, current)}
    navigation = json.loads(rendered["research_materials"])
    assert navigation == {"index_artifact_id": latest.id, "index": full_index.model_dump(mode="json")}
    assert [group["title"] for group in navigation["index"]["groups"]] == [
        "Measure the baseline", "Measure accuracy",
    ]
    material = json.loads(rendered[f"material_{feedback_ref.id}"])["content"]
    assert material["brief"] == feedback.brief.model_dump(mode="json")
    assert material["brief"]["statements"][0]["artifact_ids"] == [refs[0].id, refs[1].id]
    assert "index" not in material
    assert "index_changes" not in json.dumps(rendered)
    assert "input_artifacts" not in rendered
    assert historical.id not in rendered["research_materials"]
    assert all(ref.uri not in json.dumps(rendered) for ref in turn.input_artifacts)
    assert all(ref.sha256 not in json.dumps(rendered) for ref in turn.input_artifacts)
    assert _observed_artifact_ids(current) == []
    reader = RegisteredArtifactReader(turn.input_artifacts, run_id=RUN)
    assert json.loads(reader.read_text(historical.id)["content"])["groups"][0]["title"] == "Measure the baseline"
    tool = ReadArtifactTool(reader)
    observation = tool.execute(current, tool.input_model(artifact_id=previous_data.id))
    current.memory.update(observation.memory_updates)
    assert json.loads(observation.value["content"]) == {"accuracy": 0.7}
    assert _observed_artifact_ids(current) == [previous_data.id]


@pytest.mark.parametrize("mismatch", ["missing_ref", "unindexed_ref", "metadata"])
def test_directory_must_match_its_readable_registered_materials(tmp_path, mismatch):
    refs = handoff(tmp_path)
    if mismatch == "missing_ref":
        refs = refs[1:]
    elif mismatch == "unindexed_ref":
        refs.append(freeze(tmp_path, "artifact_extra", "data", {"value": 1}))
    else:
        refs[0] = refs[0].model_copy(update={"summary": "A different registered summary"})
    with pytest.raises(ValueError, match="research index.*match"):
        sections(request(refs, parent=SESSION))
