"""Deterministic Scientific completion check."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import ValidationError

from resagent2_contracts import (
    ArtifactRef,
    WorkTaskOutcome,
    missing_required_evidence_kinds,
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

    Shared inside Scientific by its tools, response adapter and finalizer.
    Orchestrator independently checks returned references against Run records;
    it does not import this Agent-internal helper.
    """
    return sorted(set(cited) - set(observed))


class ScientificCompletionCheck:
    """Finalize a finish candidate into a validated ScientificOpinion."""

    def __init__(
        self,
        unresolved_task_outcomes: list[WorkTaskOutcome],
        required_evidence_kinds: list[str] | None = None,
        *,
        resolve_artifact: Callable[[str], ArtifactRef | None] | None = None,
    ) -> None:
        self._unresolved = unresolved_task_outcomes
        self._required_evidence_kinds = required_evidence_kinds or []
        self._resolve_artifact = resolve_artifact

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
        unobserved = unobserved_artifact_ids(opinion.evidence_artifact_ids, observed)
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
        artifacts = []
        if self._required_evidence_kinds and self._resolve_artifact is not None:
            for artifact_id in cited:
                artifact = self._resolve_artifact(artifact_id)
                if artifact is not None:
                    artifacts.append(artifact)
        missing = missing_required_evidence_kinds(
            self._required_evidence_kinds,
            run_id=state.run_id,
            artifacts=artifacts,
            observed_artifact_ids=observed,
            cited_artifact_ids=cited,
        )
        if missing:
            return CompletionDecision(
                complete=False,
                summary=(
                    "Still missing required evidence of kind "
                    + ", ".join(repr(kind) for kind in missing)
                    + "; observe and cite registered artifacts of those kinds "
                    "before finishing. Authorized imports also count."
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
