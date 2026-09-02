"""Scientific prompt and deterministic context sections."""

from __future__ import annotations

import json

from resagent2_contracts import ScientificTurnRequest
from resagent2_runtime import AgentState, ContextSection

from .completion import _observed_artifact_ids
from .interpreter import render_work_brief


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

Use ask_user when the goal explicitly reserves a decision for the user or
forbids inferring a default (user preference, risk choice, cost limit,
evaluation metric, or whether an external action is allowed). Do not replace
an explicitly required user decision with request_work.

WorkRequest rules:
- Request only the next necessary round of work that the current evidence
  supports. Do not preemptively request repair or diagnosis before a failure has
  actually occurred: first request the experiment run; only after it fails,
  request a fix.
- Be self-contained: preserve every unmet precondition and constraint from the
  goal. Do not describe only the final evidence you want; also describe the
  problems that must be solved before that evidence can be produced.
- When the work brief lists blocking items, diagnose the failure first: read
  the blocking item's diagnostic_excerpt (why it failed) and, if needed, the
  relevant artifacts before deciding the next step.
- Do not mechanically repeat a previous WorkRequest. If you retry, state what
  condition has changed that makes the retry likely to succeed.
- If the failure is due to unimplemented code, a code bug, or an incomplete
  experiment implementation, request fixing the implementation first, then
  re-obtaining the evidence. Still never emit capability names or task ids.

Evidence citation rules:
- A work brief reports execution status and available evidence. An artifact id
  listed under "evidence" does NOT mean you have observed its contents; if your
  judgment relies on an artifact's contents, call read_artifact first.
- "narrative" fields are explanatory only: module-provided explanatory prose,
  not verified evidence. A blocking item's "diagnostic_excerpt" is execution
  diagnosis only, and "caveats" are machine-labelled delivery gaps; never treat
  any of them as observed evidence or fill an artifact id into your evidence
  list just because one of these mentions it.
- Never cite an unread artifact just to make the assessment look complete.

Execution-limitations rule:
- When the work brief lists blocking items, state at least one limitation that
  explains how incomplete execution affects the scientific conclusion. The
  Controller records exact failed/blocked task identities separately; do not
  emit task ids yourself.

Do not fabricate evidence, do not pretend an observed Artifact supports a claim
it does not, and do not write machine state yourself.

Tool arguments:
- read_artifact: {"artifact_id": "artifact_..."}
- literature_search: {"query": "...", "max_results": 10, "start_year": null, "end_year": null}
- request_work: {"assessment": {"statement": "...", "evidence_artifact_ids": [...], "limitations": [], "unresolved_questions": []}, "work_request": {"objective": "...", "expected_evidence": ["..."], "constraints": []}}
- ask_user: {"assessment": {"statement": "...", "evidence_artifact_ids": [...], "limitations": [], "unresolved_questions": []}, "text": "...", "requested_fields": [], "reason": "..."}
- finish: {"opinion": {"verdict": "supports|refutes|inconclusive|not_applicable", "statement": "...", "evidence_artifact_ids": [...], "limitations": [], "unresolved_questions": [], "recommended_next_steps": []}, "summary": "..."}
"""


def _evidence_control_state(turn: ScientificTurnRequest, state: AgentState) -> dict:
    """Derive the deterministic "read evidence before concluding" control state.

    The Scientific Agent must not rely on its own memory that a cited artifact is
    still unread: that is an outstanding obligation, and the model can lose it
    under a flood of observations. This state is recomputed every turn and shown
    at the highest priority so the obligation stays visible until the artifacts
    are actually read or dropped from the citation.
    """
    observed = _observed_artifact_ids(state)
    authorized = [artifact.id for artifact in turn.authorized_artifacts]
    unobserved_authorized = sorted(set(authorized) - set(observed))
    pending = state.memory.get("pending_citation_artifact_ids", [])
    pending = list(pending) if isinstance(pending, list) else []
    return {
        "observed_artifact_ids": observed,
        "unobserved_authorized_artifact_ids": unobserved_authorized,
        "pending_citation_artifact_ids": pending,
        "required_next_action": (
            "read_artifact_or_remove_citation" if pending else "none"
        ),
    }


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
    brief = render_work_brief(
        work_outcome=turn.work_outcome,
        previous_work_request=turn.previous_work_request,
        unresolved_task_outcomes=turn.unresolved_task_outcomes,
        authorized_artifacts=turn.authorized_artifacts,
    )
    answers = [answer.model_dump(mode="json") for answer in turn.answers]

    sections = [
        ContextSection(
            name="evidence_control_state",
            content=(
                "Current evidence control state (deterministic — do not invent "
                "your own):\n"
                + json.dumps(_evidence_control_state(turn, state), ensure_ascii=False)
            ),
            priority=1000,
            required=True,
        ),
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
            name="work_brief",
            content=json.dumps(brief, ensure_ascii=False),
            priority=90,
            required=True,
        ),
        ContextSection(
            name="answers",
            content=json.dumps(answers, ensure_ascii=False),
            priority=80,
            required=True,
        ),
    ]
    summaries = state.memory.get("read_artifact_summaries", {})
    if isinstance(summaries, dict) and summaries:
        sections.append(
            ContextSection(
                name="read_artifact_summaries",
                content=json.dumps(summaries, ensure_ascii=False),
                priority=50,
            )
        )
    return sections
