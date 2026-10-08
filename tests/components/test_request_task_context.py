"""Current task dialogue contains only verified answers for its invocation."""

import hashlib
import json
from datetime import UTC, datetime

import pytest

from resagent2_components import ArtifactReadError, request_task_context
from resagent2_contracts import (
    AgentOwner, AgentPermissions, AgentRequest, ArtifactRef, RecordedAnswer, TaskBudget,
)
from resagent2_runtime import AgentState
from resagent2_coding.context import build_context as coding_context
from resagent2_experiment.context import build_context as experiment_context
from resagent2_scientific.context import build_context as scientific_context


RUN = "run_dialogue"
SESSION = "session_dialogue"
TASK = "task_dialogue"
ANSWERED_AT = datetime(2026, 10, 7, 1, 2, 3, tzinfo=UTC)


def freeze_answer(root, artifact_id, *, task_id=None, attempt_number=None,
                  session_id=None, body_scope=None):
    scope = {
        "task_id": task_id, "attempt_number": attempt_number, "session_id": session_id,
    }
    answer = RecordedAnswer(
        run_id=RUN, question_id=f"question_{artifact_id}",
        question_text="Which device should be used?", requested_fields=["device"],
        values={"device": "cuda"}, answered_at=ANSWERED_AT, **(body_scope or scope),
    )
    path = root / f"{artifact_id}.json"
    path.write_text(answer.model_dump_json(), encoding="utf-8")
    return ArtifactRef(
        id=artifact_id, kind="answer", producer=AgentOwner.ORCHESTRATOR, run_id=RUN,
        uri=path.as_uri(), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        media_type="application/json", summary="Recorded device decision",
        metadata={"source_type": "controller_answer"}, **scope,
    )


def request(owner, refs):
    scope = {} if owner == AgentOwner.SCIENTIFIC else {
        "task_id": TASK, "attempt_number": 1,
    }
    return AgentRequest(
        run_id=RUN, agent=owner, instruction="Use the user's device decision",
        input_artifacts=refs, parent_session_id=SESSION,
        budget=TaskBudget(max_llm_calls=10, timeout_seconds=60),
        permissions=AgentPermissions(), **scope,
    )


@pytest.mark.parametrize("owner,builder,section_name", [
    (AgentOwner.CODING, coding_context, "task"),
    (AgentOwner.EXPERIMENT, experiment_context, "task"),
    (AgentOwner.SCIENTIFIC, scientific_context, "research"),
])
def test_context_builders_preserve_serializable_user_dialogue(
    tmp_path, owner, builder, section_name,
):
    scope = {"session_id": SESSION} if owner == AgentOwner.SCIENTIFIC else {
        "task_id": TASK, "attempt_number": 1,
    }
    ref = freeze_answer(tmp_path, "artifact_current_answer", **scope)
    invocation = request(owner, [ref])
    state = AgentState(
        session_id=SESSION, agent_name=owner.value, owner=owner, run_id=RUN,
        task_id=invocation.task_id, attempt_number=invocation.attempt_number,
        created_at=ANSWERED_AT, updated_at=ANSWERED_AT,
    )
    sections = builder(invocation, state)
    dialogue = json.loads(next(section.content for section in sections if section.name == section_name))
    assert dialogue["instruction"] == invocation.instruction
    assert dialogue["user_answers"] == [{
        "artifact_id": ref.id, "question_id": "question_artifact_current_answer",
        "question": "Which device should be used?", "values": {"device": "cuda"},
        "answered_at": "2026-10-07T01:02:03Z",
    }]


@pytest.mark.parametrize("foreign_scope", [
    {"task_id": "task_other", "attempt_number": 1},
    {"task_id": TASK, "attempt_number": 2},
    {"session_id": "session_other"},
])
def test_execution_dialogue_excludes_foreign_answers(tmp_path, foreign_scope):
    current = freeze_answer(tmp_path, "artifact_current", task_id=TASK, attempt_number=1)
    foreign = freeze_answer(tmp_path, "artifact_foreign", **foreign_scope)
    dialogue = request_task_context(request(AgentOwner.CODING, [foreign, current]))
    assert [answer["artifact_id"] for answer in dialogue["user_answers"]] == [current.id]


@pytest.mark.parametrize("foreign_scope", [
    {"session_id": "session_other"},
    {"task_id": TASK, "attempt_number": 1},
])
def test_scientific_dialogue_excludes_foreign_answers(tmp_path, foreign_scope):
    current = freeze_answer(tmp_path, "artifact_current", session_id=SESSION)
    foreign = freeze_answer(tmp_path, "artifact_foreign", **foreign_scope)
    dialogue = request_task_context(request(AgentOwner.SCIENTIFIC, [foreign, current]))
    assert [answer["artifact_id"] for answer in dialogue["user_answers"]] == [current.id]


def test_matching_answer_still_requires_body_scope_consistency(tmp_path):
    ref = freeze_answer(
        tmp_path, "artifact_answer", task_id=TASK, attempt_number=1,
        body_scope={"task_id": "task_other", "attempt_number": 1},
    )
    with pytest.raises(ArtifactReadError, match="does not belong"):
        request_task_context(request(AgentOwner.CODING, [ref]))


def test_matching_answer_still_requires_frozen_bytes(tmp_path):
    ref = freeze_answer(tmp_path, "artifact_answer", session_id=SESSION)
    path = tmp_path / "artifact_answer.json"
    path.write_text(path.read_text(encoding="utf-8").replace("cuda", "cpu"), encoding="utf-8")
    with pytest.raises(ArtifactReadError, match="sha256"):
        request_task_context(request(AgentOwner.SCIENTIFIC, [ref]))


@pytest.mark.parametrize("owner", [AgentOwner.CODING, AgentOwner.EXPERIMENT, AgentOwner.SCIENTIFIC])
def test_task_context_keeps_previous_answers_after_next_resume(tmp_path, owner):
    scope = {"session_id": SESSION} if owner == AgentOwner.SCIENTIFIC else {
        "task_id": TASK, "attempt_number": 1,
    }
    first = freeze_answer(tmp_path, "artifact_first_answer", **scope)
    latest = freeze_answer(tmp_path, "artifact_latest_answer", **scope)
    invocation = request(owner, [first, latest]).model_copy(update={
        "resume_artifact_ids": [latest.id],
    })
    before = invocation.model_dump_json()
    dialogue = request_task_context(invocation)
    assert [answer["artifact_id"] for answer in dialogue["user_answers"]] == [first.id, latest.id]
    assert invocation.model_dump_json() == before


def test_explicit_resume_rejects_answer_for_another_session(tmp_path):
    ref = freeze_answer(
        tmp_path, "artifact_foreign_session", session_id="session_other",
    )
    invocation = request(AgentOwner.SCIENTIFIC, [ref]).model_copy(update={
        "resume_artifact_ids": [ref.id],
    })
    with pytest.raises(ArtifactReadError, match="does not belong"):
        request_task_context(invocation)


def test_fresh_scientific_invocation_can_use_its_run_session_answer(tmp_path):
    from resagent2_contracts import scientific_session_id

    ref = freeze_answer(
        tmp_path, "artifact_run_answer", session_id=scientific_session_id(RUN),
    )
    invocation = request(AgentOwner.SCIENTIFIC, [ref]).model_copy(update={"parent_session_id": None})
    assert request_task_context(invocation)["user_answers"][0]["artifact_id"] == ref.id
