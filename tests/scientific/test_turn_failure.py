"""Scientific failures and caches remain bound to the owning session."""

from resagent2_contracts import AgentPermissions

from datetime import UTC, datetime
import json

import pytest

from resagent2_contracts import (
    AgentOwner, AgentRequest, ErrorCode, SessionStatus, TaskBudget, scientific_session_id,
)
from resagent2_runtime import AgentState, JsonSessionStore, ScriptedLLMClient
from resagent2_scientific import ScientificAgent


def request(parent=None):
    return AgentRequest(run_id='run_failure', agent=AgentOwner.SCIENTIFIC, parent_session_id=parent, instruction='Evaluate the result', budget=TaskBudget(max_llm_calls=5, timeout_seconds=30), permissions=AgentPermissions(execute_commands=True, prepare_environment=True))


def state(*, foreign=False):
    now = datetime.now(UTC)
    return AgentState(
        session_id=scientific_session_id("run_failure"), agent_name="scientific",
        owner=AgentOwner.SCIENTIFIC, run_id="run_foreign" if foreign else "run_failure",
        status=SessionStatus.PAUSED, created_at=now, updated_at=now,
    )


@pytest.mark.parametrize("artifacts", [[], [{
    "kind": "scientific_opinion", "path": "opinion.json", "media_type": "application/json",
    "summary": "Invalid", "content": "{}",
}], [{
    "kind": "scientific_opinion", "path": "opinion.json", "media_type": "application/json",
    "summary": "Unread", "content": json.dumps({
        "verdict": "supports", "statement": "Claims unread evidence",
        "evidence_artifact_ids": ["artifact_unread"],
    }),
}]])
def test_invalid_finish_settles_owned_session_and_is_idempotent(tmp_path, artifacts):
    client = ScriptedLLMClient([{"tool": "finish", "arguments": {"report": "Done", "artifacts": artifacts}}])
    store = JsonSessionStore(tmp_path / "sessions")
    agent = ScientificAgent(client, store=store)
    result = agent.invoke(request())
    persisted = store.load(result.session.id)
    assert result.status == "failed"
    assert persisted.status == SessionStatus.FAILED
    assert persisted.events[-1].type == "error"
    repeated = agent.invoke(request())
    assert repeated.error == result.error
    assert repeated.llm_calls == 0


@pytest.mark.parametrize("cached", [False, True])
def test_foreign_session_cannot_supply_or_receive_cache(tmp_path, cached):
    prior = state(foreign=True)
    if cached:
        prior.memory["_invocation_results"] = {"resume": {
            "status": "completed", "report": "FOREIGN_CACHE_CONTENT", "artifacts": [],
        }}
    store = JsonSessionStore(tmp_path / "sessions")
    store.save(prior)
    client = ScriptedLLMClient([])
    result = ScientificAgent(client, store=store).invoke(request(prior.session_id))
    assert result.status == "failed"
    assert result.error.code == ErrorCode.CONTRACT_ERROR
    assert result.session is None
    assert "FOREIGN_CACHE_CONTENT" not in result.model_dump_json()
    assert store.load(prior.session_id) == prior
    assert client.contexts == []
