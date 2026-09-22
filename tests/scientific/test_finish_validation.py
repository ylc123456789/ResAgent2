"""Scientific output mistakes are corrected inside the existing Agent loop."""

from datetime import UTC, datetime
import json

import pytest

from resagent2_components import RegisteredArtifactReader
from resagent2_contracts import (
    AgentOwner, AgentPermissions, AgentRequest, ArtifactCandidate, ErrorCode,
    SessionStatus, TaskBudget, scientific_session_id,
)
from resagent2_orchestrator.artifacts import ArtifactRegistry
from resagent2_runtime import (
    AgentState, FinishCandidate, InMemorySessionStore, NativeToolCall, ToolCallTurn,
)
from resagent2_runtime.budget import execution_budget
from resagent2_scientific import ScientificAgent
from resagent2_scientific.completion import ScientificCompletionCheck


RUN_ID = "run_finish_validation"
SESSION_ID = scientific_session_id(RUN_ID)


def opinion():
    return ArtifactCandidate(
        kind="scientific_opinion", path="opinion.json", media_type="application/json",
        summary="Scientific judgment", content=json.dumps({
            "verdict": "inconclusive", "statement": "No measurements supplied",
        }),
    )


def finish(*extras):
    return FinishCandidate(report="Scientific conclusion", artifacts=[opinion(), *extras])


def state():
    now = datetime.now(UTC)
    return AgentState(
        session_id=SESSION_ID, agent_name="scientific", owner=AgentOwner.SCIENTIFIC,
        run_id=RUN_ID, created_at=now, updated_at=now,
    )


class NativeClient:
    tool_session_key = "scientific-finish-test"

    def __init__(self, finishes):
        self.finishes = finishes
        self.contexts = []

    def next_tool_call(self, context, schemas, turns, **kwargs):
        self.contexts.append(context)
        index = len(self.contexts) - 1
        return ToolCallTurn(tool_calls=[NativeToolCall(
            id=f"finish_{index}", name="finish",
            arguments=self.finishes[index].model_dump_json(),
        )])


def invoke(client, *, budget=5, artifacts=()):
    store = InMemorySessionStore()
    request = AgentRequest(
        run_id=RUN_ID, agent="scientific", instruction="Judge the supplied evidence",
        budget=TaskBudget(max_llm_calls=budget, timeout_seconds=30),
        permissions=AgentPermissions(), input_artifacts=list(artifacts),
    )
    agent = ScientificAgent(client, store=store)
    with execution_budget(max_llm_calls=budget, timeout_seconds=30) as scope:
        result = agent.invoke(request)
        assert result.llm_calls == scope.usage.used == len(client.contexts)
    return result, store.load(SESSION_ID)


def unsupported(kind="data"):
    return ArtifactCandidate(
        kind=kind, path="copied_evidence.json", media_type="application/json",
        summary="Copied evidence", content='{"accuracy": 0.52}',
    )


def test_native_finish_feedback_corrects_kind_in_same_session_and_budget():
    client = NativeClient([finish(unsupported()), finish()])
    result, persisted = invoke(client)

    assert result.status == "completed"
    assert result.session.id == persisted.session_id == SESSION_ID
    assert result.llm_calls == persisted.llm_calls_used == 2
    assert "Unsupported Scientific finish artifact kind: data" in client.contexts[1].text
    assert "scientific_opinion.evidence_artifact_ids" in client.contexts[1].text
    assert {item.kind for item in result.artifacts} == {"scientific_opinion", "observation_trace"}
    assert persisted.status == SessionStatus.COMPLETED
    assert len(persisted.tool_turns) == 2
    assert all(len(turn.tool_results) == 1 for turn in persisted.tool_turns)
    assert persisted.runtime_feedback is None


@pytest.mark.parametrize("budget,expected_code,calls", [
    (2, ErrorCode.BUDGET_EXHAUSTED, 2),
    (10, ErrorCode.TOOL_FAILED, 5),
])
def test_invalid_finishes_stop_under_existing_budget_and_failure_limit(budget, expected_code, calls):
    client = NativeClient([finish(unsupported())] * 6)
    result, persisted = invoke(client, budget=budget)

    assert result.status == "failed"
    assert result.error.code == expected_code
    assert result.llm_calls == persisted.llm_calls_used == calls
    assert persisted.status == SessionStatus.FAILED
    assert "data" in persisted.runtime_feedback.summary
    assert not any(item.kind == "data" for item in result.artifacts)


@pytest.mark.parametrize("kind", ["invented_kind", "observation_trace", "literature_search"])
def test_model_cannot_author_unknown_or_tool_generated_scientific_artifacts(kind):
    decision = ScientificCompletionCheck([]).evaluate(state(), finish(unsupported(kind)))
    assert not decision.complete
    assert kind in decision.report


@pytest.mark.parametrize("ownership", ["input", "foreign_session", "unregistered", "modified_ref"])
def test_existing_refs_cannot_be_relabelled_as_new_outputs(tmp_path, ownership):
    registry = ArtifactRegistry(tmp_path / "artifacts")
    ref = registry.register_scientific(
        ArtifactCandidate(kind="module_report", path="report.md", media_type="text/markdown",
                          summary="Existing report", content="Existing evidence"),
        run_id=RUN_ID, session_id="session_other" if ownership == "foreign_session" else SESSION_ID,
    )
    output = ref.model_copy(update={"summary": "Rewritten metadata"}) if ownership == "modified_ref" else ref
    check = ScientificCompletionCheck(
        [], resolve_artifact=lambda _: None if ownership == "unregistered" else ref,
        input_artifact_ids=[ref.id] if ownership == "input" else [],
    )
    decision = check.evaluate(state(), finish(output))
    assert not decision.complete
    assert "cite existing evidence IDs" in decision.report


def test_live_owned_tool_ref_remains_a_valid_output(tmp_path):
    registry = ArtifactRegistry(tmp_path / "artifacts")
    ref = registry.register_scientific(
        ArtifactCandidate(kind="literature_search", path="papers.json", media_type="application/json",
                          summary="Retrieved literature", content='{"papers": []}'),
        run_id=RUN_ID, session_id=SESSION_ID,
    )
    reader = RegisteredArtifactReader([], run_id=RUN_ID, resolve=lambda _: ref)
    check = ScientificCompletionCheck([], resolve_artifact=reader.resolve_ref, reader=reader)
    decision = check.evaluate(state(), finish(ref))
    assert decision.complete
    assert decision.artifacts[-1] == ref
    duplicate = check.evaluate(state(), finish(ref, ref))
    assert not duplicate.complete
    assert "only once" in duplicate.report


def test_duplicate_logical_output_name_gets_completion_feedback():
    candidate = finish(ArtifactCandidate(
        kind="module_report", path="report.md", media_type="text/markdown",
        summary="Report", content="Conclusion", output_name="conclusion",
    ))
    candidate.artifacts[0].output_name = "conclusion"
    decision = ScientificCompletionCheck([]).evaluate(state(), candidate)
    assert not decision.complete
    assert "unique output_name" in decision.report


def test_native_finish_rejects_even_same_session_input_ref_then_recovers(tmp_path):
    registry = ArtifactRegistry(tmp_path / "artifacts")
    ref = registry.register_scientific(
        ArtifactCandidate(kind="module_report", path="prior_report.md", media_type="text/markdown",
                          summary="Earlier result", content="Earlier evidence"),
        run_id=RUN_ID, session_id=SESSION_ID,
    )
    client = NativeClient([finish(ref), finish()])
    result, persisted = invoke(client, artifacts=[ref])

    assert result.status == "completed"
    assert result.llm_calls == persisted.llm_calls_used == 2
    assert "Input, foreign or unregistered artifacts" in client.contexts[1].text
    assert all(getattr(item, "id", None) != ref.id for item in result.artifacts)
