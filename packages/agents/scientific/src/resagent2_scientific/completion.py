"""Validate a scientific opinion artifact against observed evidence."""

from __future__ import annotations

import json
from collections.abc import Callable

from pydantic import ValidationError

from resagent2_contracts import (
    AgentOwner, ArtifactCandidate, ArtifactRef, ScientificOpinion, WorkTaskOutcome,
    missing_required_evidence_kinds, SCIENTIFIC_ARTIFACT_KINDS,
    SYSTEM_GENERATED_ARTIFACT_KINDS,
)
from resagent2_components import RegisteredArtifactReader, read_artifact_json
from resagent2_runtime import AgentState, CompletionDecision, FinishCandidate


SCIENTIFIC_FINISH_ARTIFACT_KINDS = SCIENTIFIC_ARTIFACT_KINDS - SYSTEM_GENERATED_ARTIFACT_KINDS


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
        input_artifact_ids: list[str] | None = None,
    ) -> None:
        self._unresolved = unresolved_task_outcomes
        self._required_evidence_kinds = required_evidence_kinds or []
        self._resolve_artifact = resolve_artifact
        self._reader = reader
        self._input_artifact_ids = frozenset(input_artifact_ids or [])

    def evaluate(self, state: AgentState, candidate: FinishCandidate | None) -> CompletionDecision:
        if candidate is None:
            return CompletionDecision(complete=False)
        output_error = self._output_error(state, candidate)
        if output_error is not None:
            return CompletionDecision(complete=False, report=output_error)
        opinions = [item for item in candidate.artifacts if item.kind == "scientific_opinion"]
        if len(opinions) != 1:
            return CompletionDecision(
                complete=False, report="Submit exactly one scientific_opinion JSON artifact",
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

    def _output_error(self, state: AgentState, candidate: FinishCandidate) -> str | None:
        returned_ids = set()
        output_names = set()
        for item in candidate.artifacts:
            if isinstance(item, ArtifactRef):
                registered = self._resolve_artifact(item.id) if self._resolve_artifact else None
                if (
                    registered != item or item.run_id != state.run_id
                    or item.producer != AgentOwner.SCIENTIFIC
                    or item.session_id != state.session_id
                    or item.id in self._input_artifact_ids
                ):
                    return (
                        "Input, foreign or unregistered artifacts cannot be returned as new outputs; "
                        "cite existing evidence IDs in scientific_opinion.evidence_artifact_ids"
                    )
                if item.id in returned_ids:
                    return "Return each registered artifact only once"
                returned_ids.add(item.id)
            elif item.kind not in SCIENTIFIC_FINISH_ARTIFACT_KINDS:
                return (
                    f"Unsupported Scientific finish artifact kind: {item.kind}. "
                    "New artifact kinds allowed: " + ", ".join(sorted(SCIENTIFIC_FINISH_ARTIFACT_KINDS))
                    + ". Cite existing evidence IDs in scientific_opinion.evidence_artifact_ids; "
                    "tool and system records are returned automatically"
                )
            if item.output_name is not None:
                if item.output_name in output_names:
                    return "Use a unique output_name for each output artifact"
                output_names.add(item.output_name)
        return None
