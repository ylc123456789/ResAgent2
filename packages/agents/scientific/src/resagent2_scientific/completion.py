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


class ScientificCompletionCheck:
    """Finalize a finish candidate into a validated ScientificOpinion."""

    def __init__(self, unresolved_task_outcomes: list[WorkTaskOutcome]) -> None:
        self._unresolved = unresolved_task_outcomes

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

        # Every still-failed/blocked task must be acknowledged.
        unresolved_ids = {task.task_id for task in self._unresolved}
        acknowledged = set(opinion.acknowledged_task_ids)
        missing = sorted(unresolved_ids - acknowledged)
        if missing:
            return CompletionDecision(
                complete=False,
                summary=(
                    "Acknowledge the failed/blocked tasks in the opinion: "
                    + ", ".join(missing)
                ),
            )

        return CompletionDecision(
            complete=True,
            summary=finish.summary,
            payload={"opinion": opinion.model_dump(mode="json")},
        )
