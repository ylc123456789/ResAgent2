"""Deterministic Scientific completion check."""

from __future__ import annotations

from pydantic import ValidationError

from resagent2_contracts import (
    ScientificOpinion,
    WorkTaskOutcome,
)
from resagent2_runtime import (
    AgentState,
    CompletionDecision,
    FinishCandidate,
)

from .models import ScientificFinish


def _observed_artifact_ids(state: AgentState) -> list[str]:
    """Derive the session-cumulative observed artifact ids from Tool memory.

    Only successful read_artifact / literature_search observations count; the
    LLM action payload is never trusted for this list.
    """
    read = state.memory.get("read_artifact_ids", [])
    literature = state.memory.get("literature_artifact_ids", [])
    seen: list[str] = []
    for value in (*read, *literature):
        if value not in seen:
            seen.append(value)
    return seen


def unobserved_artifact_ids(cited: list[str], observed: list[str]) -> list[str]:
    """Return the cited artifact ids absent from the observed set.

    This is the single pure helper shared by the Tool layer, the Session
    finalizer and the orchestrator gate (ADR-0011 §5.3), so the three layers no
    longer each re-implement the same set subtraction.
    """
    return sorted(set(cited) - set(observed))


def _evidence_kind_ids(state: AgentState, kind: str) -> set[str]:
    """Return observed artifact ids of a required evidence kind, if known."""
    if kind == "literature_search":
        return set(state.memory.get("literature_artifact_ids", []))
    return set()


class ScientificCompletionCheck:
    """Finalize a finish candidate into a validated ScientificOpinion."""

    def __init__(
        self,
        unresolved_task_outcomes: list[WorkTaskOutcome],
        required_evidence_kinds: list[str] | None = None,
    ) -> None:
        self._unresolved = unresolved_task_outcomes
        self._required_evidence_kinds = required_evidence_kinds or []

    def evaluate(
        self,
        state: AgentState,
        candidate: FinishCandidate | None,
    ) -> CompletionDecision:
        if candidate is None:
            return CompletionDecision(complete=False)

        try:
            finish = ScientificFinish.model_validate(candidate.result)
        except ValidationError as error:
            return CompletionDecision(
                complete=False,
                summary=f"Finish result is invalid: {error.errors()[0]['msg']}",
            )

        opinion = finish.opinion
        observed = _observed_artifact_ids(state)

        # Cross-check every cited evidence id against the observation history.
        cited = set(opinion.evidence_artifact_ids)
        unobserved = sorted(cited - set(observed))
        if unobserved:
            return CompletionDecision(
                complete=False,
                summary=(
                    "Cite only observed evidence before finishing: "
                    + ", ".join(unobserved)
                ),
            )

        # Required evidence kinds: a run that must cite a certain kind of
        # registered artifact cannot finish until the opinion cites one.
        for kind in self._required_evidence_kinds:
            if not (cited & _evidence_kind_ids(state, kind)):
                return CompletionDecision(
                    complete=False,
                    summary=(
                        f"Still missing required evidence of kind {kind!r}; cite "
                        f"at least one registered {kind} artifact before finishing."
                    ),
                )

        # Failed/blocked work is a controller-owned fact, not an identifier the
        # Scientific Agent must echo. The final report renders the exact
        # execution issues from the Run; Scientific expresses only their
        # scientific impact through limitations.
        if self._unresolved and not opinion.limitations:
            return CompletionDecision(
                complete=False,
                summary=(
                    "State at least one limitation before finishing because "
                    "failed or blocked execution work remains."
                ),
            )

        return CompletionDecision(
            complete=True,
            summary=finish.summary,
            payload={"opinion": opinion.model_dump(mode="json")},
        )
