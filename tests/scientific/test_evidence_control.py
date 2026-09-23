"""Scientific evidence checks operate on opinion artifacts and real reads."""

from datetime import UTC, datetime
import json
from types import SimpleNamespace

from resagent2_contracts import AgentOwner, ArtifactCandidate
from resagent2_runtime import AgentState, FinishCandidate
from resagent2_scientific.completion import ScientificCompletionCheck
from resagent2_scientific.context import _evidence_control_state
from resagent2_scientific.tools import _unobserved_evidence


def _state(memory=None):
    now = datetime.now(UTC)
    return AgentState(
        session_id="session_s", agent_name="scientific", owner=AgentOwner.SCIENTIFIC,
        run_id="run_s", created_at=now, updated_at=now, memory=memory or {},
    )


def _finish():
    return FinishCandidate(report="Supported", artifacts=[ArtifactCandidate(
        kind="scientific_opinion", path="opinion.json", media_type="application/json",
        summary="Conclusion", content=json.dumps({
            "verdict": "supports", "statement": "Supported", "evidence_artifact_ids": ["artifact_1"],
        }),
    )])


def test_completion_rejects_unread_citation():
    decision = ScientificCompletionCheck([]).evaluate(_state(), _finish())
    assert not decision.complete
    assert "artifact_1" in decision.report


def test_completion_accepts_read_citation():
    decision = ScientificCompletionCheck([]).evaluate(
        _state({"read_artifact_ids": ["artifact_1"]}), _finish(),
    )
    assert decision.complete


def test_unobserved_evidence_filters_real_reads():
    assert _unobserved_evidence(
        _state({"read_artifact_ids": ["artifact_a"]}), ["artifact_a", "artifact_b"],
    ) == ["artifact_b"]


def test_control_state_does_not_repeat_private_authorization_catalog():
    request = SimpleNamespace(input_artifacts=[
        SimpleNamespace(id="artifact_a"), SimpleNamespace(id="artifact_b"),
    ])
    control = _evidence_control_state(request, _state({"read_artifact_ids": ["artifact_a"]}))
    assert control["observed_artifact_ids"] == ["artifact_a"]
    assert "unobserved_authorized_artifact_ids" not in control
    assert "artifact_b" not in json.dumps(control)


def test_empty_control_state_requires_no_read():
    control = _evidence_control_state(SimpleNamespace(input_artifacts=[]), _state())
    assert control["pending_citation_artifact_ids"] == []
    assert control["required_next_action"] == "none"
