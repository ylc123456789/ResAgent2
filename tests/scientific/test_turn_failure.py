"""Outer Scientific signal failures must settle their own persisted Session."""

from datetime import UTC, datetime

import pytest

from resagent2_contracts import (
    AgentOwner, ErrorCode, ModuleResult, ModuleStatus, QuestionDraft,
    ResearchRequest, RunBudget, ScientificTurnRequest, SessionRef, SessionStatus,
    TaskBudget, scientific_session_id,
)
from resagent2_runtime import AgentState, JsonSessionStore, ScriptedLLMClient
from resagent2_scientific import ScientificAgent


def _request(session_id):
    return ScientificTurnRequest(
        run_id="run_translation", parent_session_id=session_id,
        research=ResearchRequest(goal="Check a scientific signal", budget=RunBudget(
            max_tasks=2, max_attempts_per_task=1, max_llm_calls=10, timeout_seconds=60,
        )),
        budget=TaskBudget(max_steps=5, max_llm_calls=5, timeout_seconds=30),
    )


def _state(status):
    now = datetime.now(UTC)
    return AgentState(
        session_id=scientific_session_id("run_translation"),
        agent_name="scientific", owner=AgentOwner.SCIENTIFIC, run_id="run_translation",
        status=status, step=2, created_at=now, updated_at=now,
    )


def _ref(state):
    return SessionRef(
        id=state.session_id, module=state.owner, status=state.status,
        state_uri=f"session://{state.session_id}",
        created_at=state.created_at, updated_at=state.updated_at,
    )


@pytest.mark.parametrize("case", [
    "invalid_completion", "invalid_work_signal", "unobserved_work", "unobserved_question",
])
def test_translation_failure_settles_owned_session_and_caches_original_error(
    tmp_path, monkeypatch, case,
):
    completed = case == "invalid_completion"
    state = _state(SessionStatus.COMPLETED if completed else SessionStatus.PAUSED)
    assessment = {"statement": "Needs more work", "evidence_artifact_ids": ["artifact_unread"]}
    if case == "unobserved_question":
        state.memory["latest_assessment"] = assessment
    store = JsonSessionStore(tmp_path / "sessions")
    store.save(state)
    common = dict(summary="Shared loop returned", session=_ref(state), llm_calls=3)
    if completed:
        module_result = ModuleResult(status=ModuleStatus.COMPLETED, payload={}, **common)
    elif case == "unobserved_question":
        module_result = ModuleResult(status=ModuleStatus.NEEDS_USER_INPUT, question=QuestionDraft(
            text="Which metric?", requested_fields=["metric"], reason="User preference",
        ), **common)
    else:
        signal = {} if case == "invalid_work_signal" else {
            "assessment": assessment,
            "work_request": {"objective": "Run more work", "expected_evidence": ["result"]},
        }
        module_result = ModuleResult(status=ModuleStatus.REQUEST_WORK, request_work=signal, **common)
    agent = ScientificAgent(ScriptedLLMClient([]), store=store)
    monkeypatch.setattr(agent.loop, "run", lambda *args, **kwargs: module_result)
    request = _request(state.session_id)
    result = agent.run(request)
    persisted = store.load(state.session_id)

    assert result.status == "failed"
    assert result.error.code == ErrorCode.CONTRACT_ERROR
    expected = (
        "valid opinion" if completed else
        "valid assessment/work_request" if case == "invalid_work_signal" else
        "not observed by any Tool"
    )
    assert expected in result.error.message
    assert result.session.id == persisted.session_id == state.session_id
    assert result.session.status == persisted.status == SessionStatus.FAILED
    assert result.llm_calls == 3
    assert persisted.events[-1].type == "error"
    assert persisted.events[-1].data["message"] == result.error.message
    assert agent.run(request) == result
    assert len(store.load(state.session_id).events) == 1


@pytest.mark.parametrize("missing_ref", [True, False])
def test_translation_failure_does_not_relabel_unowned_session(tmp_path, missing_ref):
    state = _state(SessionStatus.COMPLETED)
    state.run_id = "run_foreign"
    store = JsonSessionStore(tmp_path / "sessions")
    store.save(state)
    agent = ScientificAgent(ScriptedLLMClient([]), store=store)
    result = agent._to_turn_result(
        _request(state.session_id),
        ModuleResult(status=ModuleStatus.COMPLETED, summary="Invalid payload", payload={},
                     session=None if missing_ref else _ref(state)),
        state.session_id, 3,
    )
    assert result.status == "failed"
    assert result.session is None
    assert result.observed_artifact_ids == []
    assert store.load(state.session_id) == state


@pytest.mark.parametrize("cached", [False, True])
def test_foreign_session_cannot_supply_or_receive_turn_cache(tmp_path, cached):
    state = _state(SessionStatus.PAUSED)
    state.run_id = "run_foreign"
    state.memory["read_artifact_ids"] = ["artifact_foreign"]
    if cached:
        state.memory["_turn_results"] = {"resume": {
            "status": "completed", "session": _ref(state).model_copy(
                update={"status": SessionStatus.COMPLETED},
            ).model_dump(mode="json"),
            "opinion": {"verdict": "inconclusive", "statement": "FOREIGN_CACHE_CONTENT"},
            "observed_artifact_ids": ["artifact_foreign"], "llm_calls": 2,
        }}
    store = JsonSessionStore(tmp_path / "sessions")
    store.save(state)
    client = ScriptedLLMClient([])
    agent = ScientificAgent(client, store=store)
    result = agent.run(_request(state.session_id))

    assert result.status == "failed"
    assert result.error.code == ErrorCode.CONTRACT_ERROR
    assert result.session is None
    assert result.observed_artifact_ids == []
    assert "FOREIGN_CACHE_CONTENT" not in result.model_dump_json()
    assert store.load(state.session_id) == state
    assert client.contexts == []
