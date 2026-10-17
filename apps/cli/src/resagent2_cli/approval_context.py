"""Read original user requirements for an operation reviewer at composition."""

from __future__ import annotations

from resagent2_orchestrator.store import RunStore


def approval_context(run_store: RunStore, run_id: str) -> dict:
    """Project root user records without promoting delegated text to authority.

    Loading errors deliberately propagate: unavailable original requirements are
    a reason to request manual review, not permission to omit those requirements.
    This reads only the Run snapshot, never artifacts or private Agent sessions.
    """
    run = run_store.load(run_id)
    dialogue = [
        {
            "event_id": event.event_id,
            "created_at": event.created_at.isoformat(),
            "type": "input",
            "record": event.model_dump(mode="json", exclude_none=True),
        }
        for event in run.upstream_inputs
        if event.source == "user"
    ]
    dialogue.extend(
        {
            "event_id": answer.question_id,
            "created_at": answer.answered_at.isoformat(),
            "type": "answer",
            "record": answer.model_dump(mode="json", exclude_none=True),
        }
        for answer in run.answers
        if answer.source == "user"
        and answer.source_session_id is None
        and answer.action is None
        and answer.task_id is None
        and answer.original_question_id is None
    )
    # Match the existing task-dialogue projection, including its ID tie-breaker.
    dialogue.sort(key=lambda item: (item["created_at"], item["event_id"]))
    return {
        "run_id": run.run_id,
        "initial_request": {
            "source": "user",
            "created_at": run.created_at.isoformat(),
            "goal": run.request.goal,
            "hypothesis": run.request.hypothesis,
            "context": run.request.context,
            "constraints": list(run.request.constraints),
            "required_artifacts": list(run.request.required_artifacts),
            "required_evidence_kinds": list(run.request.required_evidence_kinds),
        },
        "business_dialogue": dialogue,
        # Historical decisions describe what the user actually reviewed. They
        # never grant permission to this or any subsequent operation.
        "prior_operation_decisions": sorted(
            [
                {
                    **answer.model_dump(mode="json", exclude_none=True),
                    "decision": answer.values.get("approve"),
                }
                for answer in run.answers
                if answer.source == "user"
                and answer.source_session_id is None
                and answer.original_question_id is None
                and answer.action is not None
            ],
            key=lambda item: (item["answered_at"], item["question_id"]),
        ),
    }
