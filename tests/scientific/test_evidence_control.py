"""Scientific evidence control state: read-before-cite as a durable obligation."""

from datetime import UTC, datetime
from types import SimpleNamespace

from resagent2_contracts import AgentOwner, ScientificOpinion, ScientificVerdict
from resagent2_runtime import AgentState

from resagent2_scientific.context import _evidence_control_state
from resagent2_scientific.models import ScientificFinish
from resagent2_scientific.tools import FinishTool, _unobserved_evidence


def _state(memory=None) -> AgentState:
    now = datetime.now(UTC)
    return AgentState(
        session_id="session_s",
        agent_name="scientific",
        owner=AgentOwner.SCIENTIFIC,
        run_id="run_s",
        created_at=now,
        updated_at=now,
        memory=memory or {},
    )


def _opinion(evidence: list[str]) -> ScientificOpinion:
    return ScientificOpinion(
        verdict=ScientificVerdict.SUPPORTS,
        statement="supported by the evidence",
        evidence_artifact_ids=evidence,
    )


def test_finish_tool_rejects_unread_citation() -> None:
    finish = ScientificFinish(opinion=_opinion(["artifact_1"]), summary="done")
    obs = FinishTool().execute(_state(), finish)

    assert obs.ok is False
    assert obs.finish_candidate is None
    assert obs.value["unobserved_artifact_ids"] == ["artifact_1"]
    assert obs.memory_updates["pending_citation_artifact_ids"] == ["artifact_1"]


def test_finish_tool_accepts_read_citation_and_clears_pending() -> None:
    state = _state({"read_artifact_ids": ["artifact_1"]})
    finish = ScientificFinish(opinion=_opinion(["artifact_1"]), summary="done")
    obs = FinishTool().execute(state, finish)

    assert obs.ok is True
    assert obs.finish_candidate is not None
    assert obs.memory_updates["pending_citation_artifact_ids"] == []


def test_unobserved_evidence_filters_read_artifacts() -> None:
    state = _state({"read_artifact_ids": ["artifact_a"]})
    assert _unobserved_evidence(state, ["artifact_a", "artifact_b"]) == ["artifact_b"]


def test_control_state_marks_pending_citation() -> None:
    turn = SimpleNamespace(
        authorized_artifacts=[
            SimpleNamespace(id="artifact_a"),
            SimpleNamespace(id="artifact_b"),
        ]
    )
    state = _state(
        {
            "read_artifact_ids": ["artifact_a"],
            "pending_citation_artifact_ids": ["artifact_b"],
        }
    )
    control = _evidence_control_state(turn, state)

    assert control["observed_artifact_ids"] == ["artifact_a"]
    assert control["unobserved_authorized_artifact_ids"] == ["artifact_b"]
    assert control["pending_citation_artifact_ids"] == ["artifact_b"]
    assert control["required_next_action"] == "read_artifact_or_remove_citation"


def test_control_state_no_pending_is_none() -> None:
    turn = SimpleNamespace(authorized_artifacts=[])
    control = _evidence_control_state(turn, _state({}))

    assert control["pending_citation_artifact_ids"] == []
    assert control["required_next_action"] == "none"
