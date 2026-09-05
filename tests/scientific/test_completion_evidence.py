"""Deterministic ScientificCompletionCheck evidence-kind requirements."""

from datetime import UTC, datetime

import pytest

from resagent2_contracts import AgentOwner, ArtifactRef, missing_required_evidence_kinds
from resagent2_runtime import AgentState, FinishCandidate
from resagent2_scientific.completion import ScientificCompletionCheck


def _state(memory: dict) -> AgentState:
    now = datetime.now(UTC)
    return AgentState(
        session_id="session_s",
        agent_name="scientific",
        owner=AgentOwner.SCIENTIFIC,
        run_id="run_r",
        memory=memory,
        created_at=now,
        updated_at=now,
    )


def _finish(evidence: list[str]) -> FinishCandidate:
    return FinishCandidate(
        result={
            "opinion": {
                "verdict": "supports",
                "statement": "supported",
                "evidence_artifact_ids": evidence,
                "limitations": [],
                "unresolved_questions": [],
                "recommended_next_steps": [],
            },
            "summary": "done",
        }
    )


def test_required_literature_evidence_blocks_completion() -> None:
    check = ScientificCompletionCheck([], ["literature_search"])
    state = _state({"read_artifact_ids": ["artifact_other_1"]})
    decision = check.evaluate(state, _finish(["artifact_other_1"]))
    assert decision.complete is False
    assert "literature_search" in decision.summary


def _registered_artifact(*, kind="literature_search", run_id="run_r") -> ArtifactRef:
    return ArtifactRef(
        id="artifact_lit_1", kind=kind, run_id=run_id,
        producer=AgentOwner.ORCHESTRATOR, metadata={"source_type": "import"},
        uri="file:///frozen/literature.json", sha256="0" * 64,
        media_type="application/json", summary="Imported evidence",
    )


@pytest.mark.parametrize("observation_key", ["read_artifact_ids", "literature_artifact_ids"])
def test_required_literature_evidence_is_satisfied_by_citation(observation_key) -> None:
    artifact = _registered_artifact()
    check = ScientificCompletionCheck(
        [], ["literature_search"], resolve_artifact=lambda _: artifact,
    )
    state = _state({observation_key: ["artifact_lit_1"]})
    decision = check.evaluate(state, _finish(["artifact_lit_1"]))
    assert decision.complete is True


def test_search_history_cannot_self_certify_an_unregistered_artifact() -> None:
    check = ScientificCompletionCheck([], ["literature_search"])
    state = _state({"literature_artifact_ids": ["artifact_lit_1"]})
    decision = check.evaluate(state, _finish(["artifact_lit_1"]))
    assert not decision.complete
    assert "literature_search" in decision.summary


def test_required_kind_comes_from_registry_not_search_memory() -> None:
    artifact = _registered_artifact(kind="experiment_result")
    check = ScientificCompletionCheck(
        [], ["literature_search"], resolve_artifact=lambda _: artifact,
    )
    state = _state({"literature_artifact_ids": [artifact.id]})
    assert not check.evaluate(state, _finish([artifact.id])).complete


@pytest.mark.parametrize(
    "registered,observed,cited,kind,run_id,missing",
    [
        (True, True, True, "literature_search", "run_r", []),
        (False, True, True, "literature_search", "run_r", ["literature_search"]),
        (True, False, True, "literature_search", "run_r", ["literature_search"]),
        (True, True, False, "literature_search", "run_r", ["literature_search"]),
        (True, True, True, "experiment_result", "run_r", ["literature_search"]),
        (True, True, True, "literature_search", "run_other", ["literature_search"]),
    ],
)
def test_shared_evidence_requirement_uses_registered_observed_cited_intersection(
    registered, observed, cited, kind, run_id, missing,
) -> None:
    artifact = _registered_artifact(kind=kind, run_id=run_id)
    assert missing_required_evidence_kinds(
        ["literature_search"], run_id="run_r",
        artifacts=[artifact] if registered else [],
        observed_artifact_ids=[artifact.id] if observed else [],
        cited_artifact_ids=[artifact.id] if cited else [],
    ) == missing
