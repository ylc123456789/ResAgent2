"""Validate a scientific opinion artifact against observed evidence."""

from __future__ import annotations

import json
from collections.abc import Callable

from pydantic import ValidationError

from resagent2_contracts import (
    ArtifactCandidate, ArtifactRef, ScientificOpinion, WorkTaskOutcome,
    missing_required_evidence_kinds,
)
from resagent2_components import RegisteredArtifactReader, read_artifact_json
from resagent2_runtime import AgentState, CompletionDecision, FinishCandidate


def _observed_artifact_ids(state: AgentState) -> list[str]:
    """Only successful artifact-reading tools update these persisted lists."""
    return list(dict.fromkeys([
        *state.memory.get("read_artifact_ids", []),
        *state.memory.get("literature_artifact_ids", []),
    ]))


def unobserved_artifact_ids(cited: list[str], observed: list[str]) -> list[str]:
    return sorted(set(cited) - set(observed))


class ScientificCompletionCheck:
    def __init__(
        self, unresolved_task_outcomes: list[WorkTaskOutcome],
        required_evidence_kinds: list[str] | None = None, *,
        resolve_artifact: Callable[[str], ArtifactRef | None] | None = None,
        reader: RegisteredArtifactReader | None = None,
    ) -> None:
        self._unresolved = unresolved_task_outcomes
        self._required_evidence_kinds = required_evidence_kinds or []
        self._resolve_artifact = resolve_artifact
        self._reader = reader

    def evaluate(self, state: AgentState, candidate: FinishCandidate | None) -> CompletionDecision:
        if candidate is None:
            return CompletionDecision(complete=False)
        opinions = [item for item in candidate.artifacts if item.kind == "scientific_opinion"]
        if len(opinions) != 1:
            return CompletionDecision(
                complete=False, report="Submit exactly one scientific_opinion JSON artifact",
            )
        if any(item.kind == "observation_trace" for item in candidate.artifacts):
            return CompletionDecision(
                complete=False, report="Observation traces are generated from actual tool records",
            )
        try:
            item = opinions[0]
            if isinstance(item, ArtifactCandidate):
                if item.content is None:
                    raise ValueError("scientific_opinion must provide JSON content")
                opinion = ScientificOpinion.model_validate_json(item.content)
            elif self._reader is not None:
                opinion = read_artifact_json(self._reader, item.id, ScientificOpinion)
            else:
                raise ValueError("Registered opinion has no authorized reader")
        except (ValueError, ValidationError) as error:
            return CompletionDecision(complete=False, report=f"Opinion artifact is invalid: {error}")
        observed = _observed_artifact_ids(state)
        unobserved = unobserved_artifact_ids(opinion.evidence_artifact_ids, observed)
        if unobserved:
            return CompletionDecision(
                complete=False, report="Cite only observed evidence before finishing: " + ", ".join(unobserved),
            )
        artifacts = []
        if self._resolve_artifact is not None:
            artifacts = [
                artifact for item_id in opinion.evidence_artifact_ids
                if (artifact := self._resolve_artifact(item_id)) is not None
            ]
        missing = missing_required_evidence_kinds(
            self._required_evidence_kinds, run_id=state.run_id, artifacts=artifacts,
            observed_artifact_ids=observed, cited_artifact_ids=opinion.evidence_artifact_ids,
        )
        if missing:
            return CompletionDecision(
                complete=False,
                report="Still missing required evidence of kind " + ", ".join(missing)
                + "; observe and cite registered artifacts of those kinds",
            )
        if self._unresolved and not opinion.limitations:
            return CompletionDecision(
                complete=False,
                report="State at least one limitation because failed or blocked execution work remains",
            )
        return CompletionDecision(
            complete=True, report=candidate.report, artifacts=list(candidate.artifacts),
        )
