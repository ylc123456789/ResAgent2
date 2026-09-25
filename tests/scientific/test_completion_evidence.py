"""Deterministic ScientificCompletionCheck evidence-kind requirements."""

from datetime import UTC, datetime
import json

import pytest

from resagent2_contracts import AgentOwner, ArtifactCandidate, ArtifactRef, missing_required_evidence_kinds
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


def _finish(evidence: list[str], *, verdict="supports") -> FinishCandidate:
    return FinishCandidate(
        report="supported",
        artifacts=[ArtifactCandidate(
            kind="scientific_opinion", path="opinion.json", media_type="application/json",
            summary="Conclusion", content=json.dumps({
                "verdict": verdict,
                "statement": "supported",
                "evidence_artifact_ids": evidence,
                "limitations": [],
                "unresolved_questions": [],
                "recommended_next_steps": [],
            }),
        )],
    )


def test_required_literature_evidence_blocks_completion() -> None:
    check = ScientificCompletionCheck([], ["literature_search"])
    state = _state({"read_artifact_ids": ["artifact_other_1"]})
    decision = check.evaluate(state, _finish(["artifact_other_1"]))
    assert decision.complete is False
    assert "literature_search" in decision.report


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
    assert decision.report == json.loads(decision.artifacts[0].content)["statement"] == "supported"


def test_scientific_finish_rejects_a_second_model_written_summary() -> None:
    candidate = _finish(["artifact_lit_1"])
    body = json.loads(candidate.artifacts[0].content)
    body["summary"] = "A competing account of the conclusion"
    candidate.artifacts[0].content = json.dumps(body)
    decision = ScientificCompletionCheck([]).evaluate(_state({}), candidate)
    assert not decision.complete
    assert "Extra inputs" in decision.report


def test_search_history_cannot_self_certify_an_unregistered_artifact() -> None:
    check = ScientificCompletionCheck([], ["literature_search"])
    state = _state({"literature_artifact_ids": ["artifact_lit_1"]})
    decision = check.evaluate(state, _finish(["artifact_lit_1"]))
    assert not decision.complete
    assert "literature_search" in decision.report


def test_required_kind_comes_from_registry_not_search_memory() -> None:
    artifact = _registered_artifact(kind="experiment_result")
    check = ScientificCompletionCheck(
        [], ["literature_search"], resolve_artifact=lambda _: artifact,
    )
    state = _state({"literature_artifact_ids": [artifact.id]})
    assert not check.evaluate(state, _finish([artifact.id])).complete


def test_module_report_cannot_satisfy_required_literature() -> None:
    artifact = _registered_artifact(kind="module_report")
    check = ScientificCompletionCheck(
        [], ["literature_search"], resolve_artifact=lambda _: artifact,
    )
    state = _state({"read_artifact_ids": [artifact.id]})

    decision = check.evaluate(state, _finish([artifact.id]))

    assert not decision.complete
    assert "literature_search" in decision.report


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


def _named_output(tmp_path, *, output_name="metrics", run_id="run_r"):
    from resagent2_orchestrator import ArtifactRegistry

    registry = ArtifactRegistry(tmp_path / "artifacts")
    return registry.register_scientific(
        ArtifactCandidate(
            kind="module_report", path="report.md", media_type="text/markdown",
            summary="Output report", content="Delivered report", output_name=output_name,
        ),
        run_id=run_id, session_id="session_s",
    )


@pytest.mark.parametrize("available_as", ["input", "observed", "literature_observed"])
def test_required_output_does_not_require_observation_or_citation(tmp_path, available_as):
    from resagent2_components import RegisteredArtifactReader

    ref = _named_output(tmp_path)
    reader = RegisteredArtifactReader(
        [ref] if available_as == "input" else [], run_id="run_r", resolve=lambda _: ref,
    )
    memory_key = "literature_artifact_ids" if available_as == "literature_observed" else "read_artifact_ids"
    state = _state({memory_key: [ref.id]} if available_as != "input" else {})
    before = state.model_dump(mode="json")
    check = ScientificCompletionCheck(
        [], required_artifacts=["metrics"], resolve_artifact=reader.resolve_ref, reader=reader,
        input_artifact_ids=[ref.id] if available_as == "input" else [],
    )
    decision = check.evaluate(state, _finish([], verdict="inconclusive"))
    assert decision.complete
    assert state.model_dump(mode="json") == before
    assert json.loads(decision.artifacts[0].content)["evidence_artifact_ids"] == []


def test_required_output_feedback_names_each_missing_delivery(tmp_path):
    from resagent2_components import RegisteredArtifactReader

    ref = _named_output(tmp_path, output_name="Metrics")
    reader = RegisteredArtifactReader([ref], run_id="run_r")
    check = ScientificCompletionCheck(
        [], required_artifacts=["metrics", "analysis"], reader=reader,
        resolve_artifact=reader.resolve_ref, input_artifact_ids=[ref.id],
    )
    decision = check.evaluate(_state({}), _finish([], verdict="inconclusive"))
    assert not decision.complete
    assert "required_artifact_missing: required artifact was not produced; subject=metrics" in decision.report
    assert "required_artifact_missing: required artifact was not produced; subject=analysis" in decision.report
    assert "Request the missing work or ask the user" in decision.report


def test_required_output_cannot_be_self_certified_by_observation_memory():
    from resagent2_components import RegisteredArtifactReader

    check = ScientificCompletionCheck(
        [], required_artifacts=["metrics"], reader=RegisteredArtifactReader([], run_id="run_r"),
    )
    decision = check.evaluate(_state({"read_artifact_ids": ["artifact_invented"]}), _finish([], verdict="inconclusive"))
    assert not decision.complete
    assert "subject=metrics" in decision.report


def test_valid_scientific_candidate_can_await_receiving_registration():
    from resagent2_components import RegisteredArtifactReader

    candidate = _finish([], verdict="inconclusive")
    candidate.artifacts.append(ArtifactCandidate(
        kind="module_report", path="analysis.md", media_type="text/markdown",
        summary="Requested analysis", content="Analysis of the supplied material",
        output_name="analysis",
    ))
    check = ScientificCompletionCheck(
        [], required_artifacts=["analysis"], reader=RegisteredArtifactReader([], run_id="run_r"),
    )
    assert check.evaluate(_state({}), candidate).complete


def test_unsupported_pending_candidate_cannot_satisfy_delivery():
    from resagent2_components import RegisteredArtifactReader

    candidate = _finish([], verdict="inconclusive")
    candidate.artifacts.append(ArtifactCandidate(
        kind="experiment_result", path="metrics.json", media_type="application/json",
        summary="Invented metrics", content="{}", output_name="metrics",
    ))
    check = ScientificCompletionCheck(
        [], required_artifacts=["metrics"], reader=RegisteredArtifactReader([], run_id="run_r"),
    )
    decision = check.evaluate(_state({}), candidate)
    assert not decision.complete
    assert "Unsupported Scientific finish artifact kind" in decision.report


@pytest.mark.parametrize("fault", ["corrupt", "missing"])
def test_required_output_read_failure_is_not_downgraded_to_missing(tmp_path, fault):
    from pathlib import Path
    from resagent2_components import ArtifactReadError, RegisteredArtifactReader

    ref = _named_output(tmp_path)
    path = Path(ref.uri.removeprefix("file://"))
    if fault == "missing":
        path.unlink()
    else:
        path.write_text("changed", encoding="utf-8")
    reader = RegisteredArtifactReader([ref], run_id="run_r")
    check = ScientificCompletionCheck(
        [], required_artifacts=["metrics"], reader=reader,
        resolve_artifact=reader.resolve_ref, input_artifact_ids=[ref.id],
    )
    with pytest.raises(ArtifactReadError):
        check.evaluate(_state({}), _finish([], verdict="inconclusive"))
