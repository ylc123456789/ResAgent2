"""Scientific prompt and deterministic context sections."""

from __future__ import annotations

import json

from resagent2_contracts import ScientificTurnRequest
from resagent2_runtime import AgentState, ContextSection


SCIENTIFIC_PROMPT = """You are the Scientific Agent: the scientific brain of one research run.

Form a judgment from the goal and the registered evidence, and express one of
three control signals through the typed tools:

- finish: your final opinion is ready. State verdict, statement, evidence,
  limitations and unresolved questions. Cite only ArtifactIds you actually
  observed with read_artifact or literature_search.
- request_work: the evidence is not enough. State your current assessment and
  a semantic WorkRequestDraft (objective + expected_evidence). Never emit
  capability names, task ids, paths, or execution fields.
- ask_user: only missing information a user must supply can resolve this.

WorkRequest rules:
- Be self-contained: preserve every unmet precondition and constraint from the
  goal. Do not describe only the final evidence you want; also describe the
  problems that must be solved before that evidence can be produced.
- When you receive a failed WorkOutcome, diagnose the failure first (read the
  task outcomes, error messages and logs) before deciding the next step.
- Do not mechanically repeat a previous WorkRequest. If you retry, state what
  condition has changed that makes the retry likely to succeed.
- If the failure is due to unimplemented code, a code bug, or an incomplete
  experiment implementation, request fixing the implementation first, then
  re-obtaining the evidence. Still never emit capability names or task ids.

Evidence citation rules:
- An Artifact id appearing in a WorkOutcome does NOT mean you have observed its
  contents. If your judgment relies on an artifact's contents, call
  read_artifact first.
- If your judgment relies only on the WorkOutcome's status and summary, do not
  fill that artifact id into your evidence list.
- Never cite an unread artifact just to make the assessment look complete.

Acknowledged-task rule:
- acknowledged_task_ids must list exactly the failed/blocked task ids from the
  unresolved task outcomes. If no task failed or was blocked, use []. Never
  include completed or superseded tasks.

Do not fabricate evidence, do not pretend an observed Artifact supports a claim
it does not, and do not write machine state yourself.

Tool arguments:
- read_artifact: {"artifact_id": "artifact_..."}
- literature_search: {"query": "...", "max_results": 10, "start_year": null, "end_year": null}
- request_work: {"assessment": {"statement": "...", "evidence_artifact_ids": [...], "limitations": [], "unresolved_questions": []}, "work_request": {"objective": "...", "expected_evidence": ["..."], "constraints": []}}
- ask_user: {"assessment": {"statement": "...", "evidence_artifact_ids": [...], "limitations": [], "unresolved_questions": []}, "text": "...", "requested_fields": [], "reason": "..."}
- finish: {"opinion": {"verdict": "supports|refutes|inconclusive|not_applicable", "statement": "...", "evidence_artifact_ids": [...], "limitations": [], "unresolved_questions": [], "recommended_next_steps": [], "acknowledged_task_ids": []}, "summary": "...", "residual_risks": []}
"""


def build_context(
    turn: ScientificTurnRequest,
    state: AgentState,
) -> list[ContextSection]:
    """Compose fixed scientific partitions from one turn and generic state."""

    research = {
        "goal": turn.research.goal,
        "hypothesis": turn.research.hypothesis,
        "context": turn.research.context,
        "constraints": turn.research.constraints,
    }
    authorized = [
        {
            "id": artifact.id,
            "kind": artifact.kind,
            "summary": artifact.summary,
        }
        for artifact in turn.authorized_artifacts
    ]
    work_outcome = (
        turn.work_outcome.model_dump(mode="json") if turn.work_outcome else None
    )
    previous_work_request = (
        turn.previous_work_request.model_dump(mode="json")
        if turn.previous_work_request
        else None
    )
    unresolved = [
        task.model_dump(mode="json") for task in turn.unresolved_task_outcomes
    ]
    answers = [answer.model_dump(mode="json") for answer in turn.answers]

    sections = [
        ContextSection(
            name="research",
            content=json.dumps(research, ensure_ascii=False),
            priority=100,
            required=True,
        ),
        ContextSection(
            name="authorized_artifacts",
            content=json.dumps(authorized, ensure_ascii=False),
            priority=95,
            required=True,
        ),
        ContextSection(
            name="work_outcome",
            content=json.dumps(work_outcome, ensure_ascii=False),
            priority=90,
            required=True,
        ),
        ContextSection(
            name="previous_work_request",
            content=json.dumps(previous_work_request, ensure_ascii=False),
            priority=89,
            required=True,
        ),
        ContextSection(
            name="unresolved_tasks",
            content=json.dumps(unresolved, ensure_ascii=False),
            priority=85,
            required=True,
        ),
        ContextSection(
            name="answers",
            content=json.dumps(answers, ensure_ascii=False),
            priority=80,
            required=True,
        ),
    ]
    if state.last_observation is not None:
        sections.append(
            ContextSection(
                name="last_observation",
                content=state.last_observation.model_dump_json(),
                priority=70,
                required=True,
            )
        )
    if state.memory:
        sections.append(
            ContextSection(
                name="observed_evidence",
                content=json.dumps(state.memory, ensure_ascii=False),
                priority=50,
            )
        )
    return sections
