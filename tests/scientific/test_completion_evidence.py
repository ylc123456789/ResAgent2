"""Deterministic ScientificCompletionCheck evidence-kind requirements."""

from datetime import UTC, datetime

from resagent2_contracts import AgentOwner
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


def test_required_literature_evidence_is_satisfied_by_citation() -> None:
    check = ScientificCompletionCheck([], ["literature_search"])
    state = _state({"literature_artifact_ids": ["artifact_lit_1"]})
    decision = check.evaluate(state, _finish(["artifact_lit_1"]))
    assert decision.complete is True
