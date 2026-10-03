"""Organize registered materials and recorded reports for Scientific.

Both projections are deterministic. The Controller owns persistence and
Scientific owns the interpretation of the delivered results.
"""
from __future__ import annotations

import json
from typing import Protocol

from resagent2_components.artifacts import RegisteredArtifactReader, research_artifacts
from resagent2_components.materials import read_artifact_json
from resagent2_contracts import (
    ArtifactRef, ResearchArtifactEntry, ResearchIndex, ResearchIndexGroup,
    RecordedAnswer, WorkRecord, WorkRequest,
)


class WorkInterpreter(Protocol):
    """Organize one paired record without reading private state or changing it."""

    def interpret(self, record: WorkRecord) -> str: ...


def build_research_index(*, run_id: str, artifacts: list[ArtifactRef],
                         work_requests: list[WorkRequest], tasks=()) -> ResearchIndex:
    """Derive navigation only; the original registry remains authoritative."""
    groups = {
        "inputs": ResearchIndexGroup(key="inputs", title="Initial research materials"),
        "scientific": ResearchIndexGroup(key="scientific", title="Scientific research materials"),
    }
    for work in work_requests:
        groups[work.id] = ResearchIndexGroup(key=work.id, title=work.request.objective)
    task_map = {task.id: task for task in tasks}
    reader = RegisteredArtifactReader(artifacts, run_id=run_id)
    for ref in research_artifacts(artifacts):
        if ref.run_id != run_id:
            raise ValueError("research index cannot include a foreign Run artifact")
        source_id = ref.metadata.get("source_artifact_id")
        if source_id is not None and reader.resolve_ref(source_id) is None:
            raise ValueError("research material has no registered source artifact in this Run")
        if ref.kind == "answer":
            answer = read_artifact_json(reader, ref.id, RecordedAnswer)
            if (answer.run_id, answer.task_id, answer.attempt_number, answer.session_id) != (
                    ref.run_id, ref.task_id, ref.attempt_number, ref.session_id):
                raise ValueError("answer provenance does not match its reference")
        status = None
        if ref.kind == "work_record":
            record = read_artifact_json(reader, ref.id, WorkRecord)
            if record.run_id != run_id or record.session_id != ref.session_id:
                raise ValueError("work record provenance does not match its reference")
            key = record.work_request_id
        elif ref.task_id is not None:
            task = task_map.get(ref.task_id)
            if task is None:
                raise ValueError("registered task artifact has no source task")
            attempt = next((a for a in task.attempts if a.number == ref.attempt_number), None)
            if attempt is None or (ref.kind != "answer" and ref.id not in attempt.artifact_ids):
                raise ValueError("registered artifact has no source attempt binding")
            key, status = task.work_request_id, attempt.status
        else:
            key = "inputs" if ref.metadata.get("source_type") == "import" else "scientific"
        if key not in groups:
            raise ValueError("registered artifact has no source work request")
        groups[key].artifacts.append(ResearchArtifactEntry.from_ref(ref, execution_status=status))
    return ResearchIndex(run_id=run_id, groups=[group for group in groups.values() if group.artifacts])


class DeterministicWorkInterpreter:
    """Deliver recorded reports without another semantic translation."""

    def interpret(self, record: WorkRecord) -> str:
        record = WorkRecord.model_validate(record)
        attempts = {task.task_id: {} for task in record.work_outcome.tasks}
        for attempt in record.attempts:
            if attempt.task_id not in attempts:
                raise ValueError("work attempt has no matching task outcome")
            by_number = attempts[attempt.task_id]
            if attempt.attempt_number in by_number:
                raise ValueError("duplicate task attempt in work record")
            by_number[attempt.attempt_number] = attempt
        request = record.previous_work_request
        parts = [
            f"Work request: {record.work_request_id}",
            "Objective:\n" + request.objective,
            "Expected evidence:\n" + "\n".join(request.expected_evidence),
        ]
        if request.constraints:
            parts.append("Constraints:\n" + "\n".join(request.constraints))
        for task in record.work_outcome.tasks:
            parts.extend([f"Task: {task.task_id}", f"Task status: {task.status}"])
            history = sorted(attempts[task.task_id].values(), key=lambda item: item.attempt_number)
            if history:
                latest = history[-1]
                if task.artifact_ids != latest.artifact_ids:
                    raise ValueError("task outcome artifacts do not match the latest attempt")
                parts.append(f"Latest attempt: {latest.attempt_number}; status: {latest.status.value}")
                parts.append("Recorded report:\n" + (latest.summary or "(No recorded report.)"))
                if len(history) > 1:
                    parts.append("Earlier attempts (reports remain in the work record):\n" +
                                 "\n".join(f"- attempt {item.attempt_number}: {item.status.value}"
                                           for item in history[:-1]))
            else:
                if task.artifact_ids:
                    raise ValueError("task outcome artifacts have no source attempt")
                parts.append("Not executed; no Agent report.")
            if task.error is not None:
                parts.append("Task error:\n" + task.error.model_dump_json(exclude={"schema_version"}))
            if task.warnings:
                parts.append("Task warnings (accumulated across attempts):\n" + json.dumps(
                    [item.model_dump(mode="json", exclude={"schema_version"}) for item in task.warnings],
                    ensure_ascii=False,
                ))
            parts.append("Output artifact IDs: " + (", ".join(task.artifact_ids) or "(none)"))
        return "\n\n".join(parts)
