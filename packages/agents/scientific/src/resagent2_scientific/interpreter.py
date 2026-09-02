"""Interpret execution outcomes as scientific decision context.

This module is a deterministic receiving-side boundary:
execution facts in, scientific work brief out.
It does not call an LLM, read files, mutate state, or schedule work.
"""

from __future__ import annotations

from pydantic import JsonValue

from resagent2_contracts import (
    ArtifactRef,
    ModuleError,
    WorkOutcome,
    WorkRequestDraft,
    WorkTaskOutcome,
)

_EXCERPT_LIMIT = 1_000


def _diagnostic_excerpt(error: ModuleError | None) -> str | None:
    """Whitelist the single actionable failure fact the Science layer may see.

    Only ``details["stderr_tail"]`` is projected, and only its bounded tail (the
    exception is in the final lines). Command text, log paths and any other
    detail stay in the audit trail, so a diagnostic excerpt can never be
    mistaken for scientific evidence.
    """
    if error is None:
        return None
    details = error.details
    if not isinstance(details, dict):
        return None
    tail = details.get("stderr_tail")
    if not isinstance(tail, str) or not tail.strip():
        return None
    return tail[-_EXCERPT_LIMIT:]


def render_work_brief(
    *,
    work_outcome: WorkOutcome | None,
    previous_work_request: WorkRequestDraft | None,
    unresolved_task_outcomes: list[WorkTaskOutcome],
    authorized_artifacts: list[ArtifactRef],
) -> dict[str, JsonValue]:
    """Render the model-facing scientific view of completed execution work.

    The raw ``WorkOutcome`` is an execution-fact summary (task ids, status
    enums, error codes, warnings). The Scientific Agent reasons over scientific
    semantics, so this lifts only what a scientist needs and labels how each
    piece may be used. Everything else stays in the audit trail untouched.
    """
    authorized = {artifact.id: artifact for artifact in authorized_artifacts}

    # The semantic request this round of work was meant to satisfy. Reused
    # verbatim from the previous turn, never re-summarized.
    purpose: JsonValue = None
    if previous_work_request is not None:
        purpose = {
            "objective": previous_work_request.objective,
            "expected_evidence": list(previous_work_request.expected_evidence),
            "constraints": list(previous_work_request.constraints),
        }

    outcomes: list[JsonValue] = []
    if work_outcome is not None:
        for task in work_outcome.tasks:
            if task.status != "completed":
                continue
            evidence: list[JsonValue] = []
            unregistered: list[str] = []
            for artifact_id in task.artifact_ids:
                artifact = authorized.get(artifact_id)
                if artifact is None:
                    unregistered.append(artifact_id)
                else:
                    evidence.append(
                        {
                            "artifact_id": artifact_id,
                            "kind": artifact.kind,
                            "use": "read_artifact_before_content_based_claims",
                        }
                    )
            entry: dict[str, JsonValue] = {
                "task_id": task.task_id,
                "execution_status": (
                    "completed_with_caveats" if task.warnings else "completed"
                ),
                "narrative": task.summary,
                "narrative_use": "explanatory_only",
                "evidence": evidence,
            }
            if task.warnings:
                entry["caveats"] = [
                    {"code": warning.code, "message": warning.message}
                    for warning in task.warnings
                ]
            if unregistered:
                entry["unregistered_artifact_ids"] = unregistered
            outcomes.append(entry)

    # Failed/blocked tasks the Scientific Agent must acknowledge before it may
    # finish. Only the stable error facts plus a bounded stderr excerpt are
    # exposed; error details stay in the audit trail.
    blocking_items: list[JsonValue] = []
    for task in unresolved_task_outcomes:
        error = task.error
        item: dict[str, JsonValue] = {
            "task_id": task.task_id,
            "status": task.status,
            "error_code": error.code.value if error is not None else None,
            "message": error.message if error is not None else task.summary,
            "retryable": error.retryable if error is not None else False,
        }
        excerpt = _diagnostic_excerpt(error)
        if excerpt is not None:
            item["diagnostic_excerpt"] = excerpt
            item["diagnostic_use"] = "execution_diagnosis_only"
        blocking_items.append(item)

    return {
        "purpose": purpose,
        "outcomes": outcomes,
        "blocking_items": blocking_items,
        "acknowledgement_required_task_ids": [
            task.task_id for task in unresolved_task_outcomes
        ],
    }
