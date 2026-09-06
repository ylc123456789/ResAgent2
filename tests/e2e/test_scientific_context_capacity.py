"""Real Scientific assembly retains bounded evidence, not a second text cache."""

import hashlib
import json

import pytest

from resagent2_contracts import (
    AgentOwner, ArtifactRef, ErrorCode, ResearchRequest, RunBudget, ScientificTurnRequest,
    TaskBudget, scientific_session_id,
)
from resagent2_runtime import ContextComposer, InMemorySessionStore, ScriptedLLMClient
from resagent2_scientific import ScientificAgent


class _UnusedLiteratureServices:
    """Install the complete native tool schema without making network calls."""

    def search(self, *args, **kwargs):
        raise AssertionError("These tests must only read registered local evidence")

    def register_scientific(self, *args, **kwargs):
        raise AssertionError("These tests must not register fabricated search evidence")


def _artifact(tmp_path, label, *, lines=60, key_line=None):
    # Fixed-width real lines make the 6000-character bound independently
    # measurable, and put evidence beyond both the old 2000-character cache
    # and Runtime's 400-character history preview.
    body = "".join(
        (f"{label}_KEY_EVIDENCE" if number == key_line else f"{label}_line_{number:03d}")
        .ljust(99, ".") + "\n"
        for number in range(1, lines + 1)
    )
    path = tmp_path / f"{label}.txt"
    path.write_text(body, encoding="utf-8")
    return ArtifactRef(
        id=f"artifact_{label}", kind="experiment_result", producer=AgentOwner.EXPERIMENT,
        run_id="run_scientific_capacity", task_id="task_experiment", attempt_number=1,
        uri=path.as_uri(), sha256=hashlib.sha256(body.encode()).hexdigest(),
        media_type="text/plain", summary=f"Registered {label} evidence",
    ), body


def _read(artifact, start=None, end=None):
    arguments = {"artifact_id": artifact.id}
    if start is not None:
        arguments.update(start_line=start, end_line=end)
    return {"tool": "read_artifact", "arguments": arguments}


def _pause():
    return {"tool": "ask_user", "arguments": {
        "assessment": {"statement": "Evidence is available for the next decision"},
        "text": "Proceed with the next evaluation?", "requested_fields": ["approval"],
        "reason": "The fixture stops after observing the composed context",
    }}


def _turn(artifacts, *, parent=None):
    return ScientificTurnRequest(
        run_id="run_scientific_capacity",
        research=ResearchRequest(
            goal="Review the supplied evidence before asking for approval",
            budget=RunBudget(max_tasks=2, max_attempts_per_task=1, max_llm_calls=20, timeout_seconds=60),
        ),
        authorized_artifacts=artifacts, parent_session_id=parent,
        budget=TaskBudget(max_steps=10, max_llm_calls=10, timeout_seconds=30),
    )


def _agent(client, store, **options):
    services = _UnusedLiteratureServices()
    return ScientificAgent(
        client, store=store, literature_backend=services, registration_port=services,
        **options,
    )


def _reads(context):
    assert "workspace_reads" in context.included_sections
    section = context.text.split("## workspace_reads\n", 1)[1].split("\n\n## ", 1)[0]
    return json.loads(section.split("\n", 1)[1])


def _assert_composed(context, limit=8192):
    assert {"workspace_reads", "tool_contracts", "evidence_control_state"} <= set(context.included_sections)
    assert "read_artifact_summaries" not in context.included_sections
    contracts = context.text.split("## tool_contracts\n", 1)[1].split("\n\n## ", 1)[0]
    for tool in ("read_artifact", "literature_search", "request_work", "ask_user", "finish"):
        assert f"{tool}:" in contracts
    assert context.estimated_tokens <= limit
    assert ContextComposer.estimate_tokens(context.text) <= limit


def test_scientific_range_read_exposes_middle_evidence_in_next_real_prompt(tmp_path):
    artifact, body = _artifact(tmp_path, "middle", lines=120, key_line=96)
    client = ScriptedLLMClient([_read(artifact), _read(artifact, 71, 120), _pause()])
    store = InMemorySessionStore()
    agent = _agent(client, store)

    result = agent.run(_turn([artifact]))

    assert agent.max_context_tokens == 8192
    assert result.status == "needs_user_input", result.model_dump(mode="json")
    assert len(client.contexts) == 3
    assert "middle_KEY_EVIDENCE" not in client.contexts[1].text
    context = client.contexts[2]
    _assert_composed(context)
    snippets = _reads(context)["artifact_snippets"]
    window = next(item for item in snippets if item["start_line"] == 71)
    expected = "".join(body.splitlines(keepends=True)[70:120])
    assert window["content"] == expected
    assert len(expected) == 5000
    assert not window["truncated"]
    assert "middle_KEY_EVIDENCE" in context.text
    assert "middle_KEY_EVIDENCE" not in expected[:2000]
    assert "middle_KEY_EVIDENCE" not in context.text.split("## recent_observations\n", 1)[1]

    state = store.load(scientific_session_id("run_scientific_capacity"))
    observations = [event for event in state.events if event.type == "observation" and event.tool == "read_artifact"]
    assert len(observations) == 2
    assert observations[0].data["value"]["content"] == body[:8000]
    assert observations[0].data["value"]["truncated"] is True
    assert observations[1].data["value"]["content"] == expected
    for event in observations:
        assert "observed_at" not in event.data["value"]
        assert "context_truncated" not in event.data["value"]
    assert "read_artifact_summaries" not in state.memory


def _run_full_pool(tmp_path):
    artifact_a, body_a = _artifact(tmp_path, "a")
    artifact_b, body_b = _artifact(tmp_path, "b")
    client = ScriptedLLMClient([
        _read(artifact_a, 1, 30), _read(artifact_b, 1, 30),
        _read(artifact_a, 31, 60), _read(artifact_b, 31, 60), _pause(),
    ])
    store = InMemorySessionStore()
    agent = _agent(client, store)
    turn = _turn([artifact_a, artifact_b])
    result = agent.run(turn)
    assert result.status == "needs_user_input", result.model_dump(mode="json")
    return agent, client, store, turn, (body_a, body_b)


def test_scientific_full_pool_keeps_multiple_artifacts_as_history_grows(tmp_path):
    agent, client, store, turn, bodies = _run_full_pool(tmp_path)
    assert agent.max_context_tokens == 8192
    assert len(client.contexts) == 5
    # Reading more ranges evicts old projections, not the entire evidence
    # section. At every full-pool request both artifact identities coexist.
    for context in client.contexts[2:]:
        _assert_composed(context)
        reads = _reads(context)
        snippets = reads["artifact_snippets"]
        assert reads["file_snippets"] == []
        assert {item["artifact_id"] for item in snippets} == {"artifact_a", "artifact_b"}
        assert sum(len(item["content"]) for item in snippets) == 6000
        assert all(not item["truncated"] for item in snippets)
    final_snippets = _reads(client.contexts[-1])["artifact_snippets"]
    assert [item["observed_at"] for item in final_snippets] == [6, 8]
    assert [item["start_line"] for item in final_snippets] == [31, 31]
    assert [item["content"] for item in final_snippets] == [body[3000:] for body in bodies]

    state = store.load(scientific_session_id(turn.run_id))
    observations = [event for event in state.events if event.type == "observation" and event.tool == "read_artifact"]
    assert [event.data["value"]["content"] for event in observations] == [
        bodies[0][:3000], bodies[1][:3000], bodies[0][3000:], bodies[1][3000:],
    ]
    assert "read_artifact_summaries" not in state.memory


@pytest.mark.parametrize("explicit_limit", [1024, 5000])
def test_scientific_explicit_context_budget_is_respected(tmp_path, explicit_limit):
    _, _, store, turn, _ = _run_full_pool(tmp_path)
    client = ScriptedLLMClient([_pause()])
    agent = _agent(client, store, max_context_tokens=explicit_limit)
    resumed = turn.model_copy(update={"parent_session_id": scientific_session_id(turn.run_id)})

    result = agent.run(resumed)

    assert agent.max_context_tokens == explicit_limit
    if explicit_limit == 1024:
        assert result.status == "failed", result.model_dump(mode="json")
        assert result.error.code == ErrorCode.BUDGET_EXHAUSTED
        assert "context" in result.error.message.lower()
        assert result.error.retryable is False
        assert result.llm_calls == 0
        assert client.contexts == []
    else:
        # This minimal turn fits within 5000. An explicit limit is a ceiling,
        # not a reason to reject a context that actually fits, nor to expand it.
        assert result.status == "needs_user_input", result.model_dump(mode="json")
        assert result.llm_calls == 1
        assert len(client.contexts) == 1
        _assert_composed(client.contexts[0], limit=explicit_limit)
        snippets = _reads(client.contexts[0])["artifact_snippets"]
        assert sum(len(item["content"]) for item in snippets) == 6000
