"""Pure rendering of persisted Run state and LLM trace records.

Every function here is a side-effect-free mapping from structured state to a
list of text lines. The interactive shell calls these; the existing one-shot
``_render_run`` also delegates here so the terminal summary stays identical in
both modes.
"""

from __future__ import annotations

from typing import Any

_TERMINAL_MARKS = {
    "pending": "○",
    "running": "●",
    "completed": "✓",
    "failed": "✗",
    "blocked": "⊘",
    "needs_user_input": "?",
}


def _truncate(text: str, width: int) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def render_live(run: Any, trace_records: list[dict] | None = None) -> list[str]:
    """One compact, redrawable block for the in-progress view.

    ``trace_records`` is the most recent batch of append-only LLM trace lines
    (metadata level only: ``agent`` and ``tool`` names, never raw content).
    """
    if run is None:
        return ["… starting …"]
    lines = [
        f"Run {run.run_id} · {run.status.value} "
        f"· LLM {run.llm_calls_used}/{run.request.budget.max_llm_calls}"
    ]
    if run.workflow is not None:
        for task in run.workflow.tasks:
            mark = _TERMINAL_MARKS.get(task.status.value, "·")
            attempts = f" · attempt {len(task.attempts)}" if task.attempts else ""
            lines.append(
                f"{mark} {task.id} [{task.capability.value}] "
                f"{task.status.value}{attempts}"
            )
            if task.goal:
                lines.append(f"     {_truncate(task.goal, 64)}")
            if task.attempts:
                latest = task.attempts[-1]
                if latest.summary:
                    lines.append(f"     {_truncate(latest.summary, 72)}")
                if latest.error is not None:
                    lines.append(
                        f"     {latest.error.code.value}: {latest.error.message}"
                    )
    if trace_records:
        record = trace_records[-1]
        activity = f"{record.get('agent')}/{record.get('tool')}"
        if record.get("step") is not None:
            activity += f" (step {record.get('step')})"
        lines.append(f"→ {activity}")
    if run.pending_question is not None:
        question = run.pending_question
        lines.append(f"? {question.text}")
        if question.requested_fields:
            lines.append(f"  answer: {', '.join(question.requested_fields)}")
    return lines


def render_final(run: Any) -> list[str]:
    """The full terminal summary (identical to the one-shot CLI output)."""
    lines = [
        f"Run: {run.run_id}",
        f"Status: {run.status.value}",
        f"Goal: {run.request.goal}",
        f"LLM calls: {run.llm_calls_used}/{run.request.budget.max_llm_calls}",
    ]
    if run.latest_scientific_assessment is not None:
        lines.append("Scientific assessment:")
        lines.append(f"  {run.latest_scientific_assessment.statement}")
    if run.workflow is not None:
        lines.append("Tasks:")
        for task in run.workflow.tasks:
            suffix = f" ({len(task.attempts)} attempt(s))" if task.attempts else ""
            lines.append(f"  {task.id}: {task.status.value}{suffix}")
            if task.attempts:
                latest = task.attempts[-1]
                if latest.summary:
                    lines.append(f"    {latest.summary}")
                if latest.error is not None:
                    lines.append(
                        f"    {latest.error.code.value}: {latest.error.message}"
                    )
    if run.pending_question is not None:
        lines.append("Pending question:")
        lines.append(f"  {run.pending_question.text}")
        if run.pending_question.requested_fields:
            fields = ", ".join(run.pending_question.requested_fields)
            lines.append(f"  Fields: {fields}")
    if run.artifacts:
        lines.append("Artifacts:")
        for artifact in run.artifacts.values():
            lines.append(
                f"  {artifact.id}: {artifact.kind} "
                f"[{artifact.producer.value}] {artifact.uri}"
            )
    if run.final_opinion is not None:
        lines.append("Final opinion:")
        lines.append(f"  Verdict: {run.final_opinion.verdict.value}")
        lines.append(f"  {run.final_opinion.statement}")
    if run.completion_violations:
        lines.append("Completion violations:")
        for violation in run.completion_violations:
            lines.append(f"  {violation.code.value}: {violation.message}")
    return lines


def render_artifacts(run: Any) -> list[str]:
    """Artifact-only view for the ``/artifacts`` command."""
    if not run.artifacts:
        return ["No artifacts."]
    lines = [f"Artifacts ({len(run.artifacts)}):"]
    for artifact in run.artifacts.values():
        lines.append(
            f"  {artifact.id}: {artifact.kind} "
            f"[{artifact.producer.value}] {artifact.uri}"
        )
    return lines


def render_trace(records: list[dict]) -> list[str]:
    """Raw LLM trace view for the ``/trace`` command (full level)."""
    if not records:
        return ["No trace records."]
    lines: list[str] = []
    for record in records:
        lines.append("-" * 40)
        meta = f"seq={record.get('sequence')} agent={record.get('agent')}"
        meta += f" tool={record.get('tool')} step={record.get('step')}"
        if record.get("latency_ms") is not None:
            meta += f" latency_ms={record.get('latency_ms')}"
        lines.append(meta)
        request = record.get("request_text")
        response = record.get("raw_response_text")
        reasoning = record.get("raw_reasoning_text")
        if request is not None:
            lines.append("[request]")
            lines.append(request)
        if reasoning:
            lines.append("[reasoning]")
            lines.append(reasoning)
        if response is not None:
            lines.append("[response]")
            lines.append(response)
    return lines
