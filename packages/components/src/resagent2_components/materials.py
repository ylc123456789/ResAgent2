"""Read structured invocation materials through the existing artifact boundary."""

from __future__ import annotations

import json
from collections.abc import Iterable
from resagent2_contracts import (
    AgentRequest, ArtifactRef, DatasetRef, RecordedAnswer, WorkFeedback, WorkRecord,
    TaskAcceptanceSpec, ConclusionRequirements, ResearchArtifactEntry, ResearchIndexGroup,
    scientific_session_id, task_session_id,
)
from resagent2_runtime import AgentState, ContextMaterial, ContextSection
from .artifacts import ArtifactReadError, RegisteredArtifactReader
from .text import slice_text_lines


def read_artifact_json(reader: RegisteredArtifactReader, artifact_id: str, model=None):
    window = reader.read_text(artifact_id, max_chars=16_000_000)
    if window.get("truncated"):
        raise ArtifactReadError("structured artifact exceeds the reading limit")
    value = json.loads(window["content"])
    return model.model_validate(value) if model is not None else value


def _request_session_id(request: AgentRequest) -> str:
    return request.parent_session_id or (
        scientific_session_id(request.run_id) if request.task_id is None else
        task_session_id(request.run_id, request.task_id, request.attempt_number)
    )


def read_request_material(request: AgentRequest, ref: ArtifactRef, *, reader=None) -> dict:
    """Verify structured material and invocation scope without choosing its presentation."""
    reader = reader or RegisteredArtifactReader(request.input_artifacts, run_id=request.run_id)
    value = read_artifact_json(reader, ref.id)
    if ref.kind == "answer":
        answer = RecordedAnswer.model_validate(value)
        if (answer.run_id != request.run_id or answer.task_id != request.task_id
                or answer.attempt_number != request.attempt_number
                or ref.task_id != answer.task_id or ref.attempt_number != answer.attempt_number
                or ref.session_id != answer.session_id
                or (answer.session_id is not None
                    and answer.session_id != _request_session_id(request))):
            raise ArtifactReadError("answer does not belong to the resumed invocation")
    elif ref.kind == "work_feedback":
        feedback = WorkFeedback.model_validate(value)
        if (request.task_id is not None or feedback.run_id != request.run_id
                or feedback.session_id != request.parent_session_id
                or ref.session_id != feedback.session_id):
            raise ArtifactReadError("work feedback does not belong to the resumed Scientific session")
    elif ref.kind == "acceptance_requirements":
        TaskAcceptanceSpec.model_validate(value)
        if ref.task_id != request.task_id:
            raise ArtifactReadError("acceptance requirements belong to another task")
    elif ref.kind == "conclusion_requirements":
        ConclusionRequirements.model_validate(value)
    return value


def request_task_context(request: AgentRequest) -> dict:
    """Project the instruction and verified user dialogue for this invocation."""
    reader = RegisteredArtifactReader(request.input_artifacts, run_id=request.run_id)
    session_id = _request_session_id(request)
    answers = []
    for ref in request.input_artifacts:
        if ref.kind != "answer":
            continue
        same_scope = (ref.task_id, ref.attempt_number) == (request.task_id, request.attempt_number)
        same_session = ref.session_id == session_id if request.task_id is None else (
            ref.session_id is None or ref.session_id == session_id
        )
        if ref.id not in request.resume_artifact_ids and not (same_scope and same_session):
            continue
        value = read_request_material(request, ref, reader=reader)
        answer = RecordedAnswer.model_validate(value).model_dump(mode="json")
        answers.append({
            "artifact_id": ref.id, "question_id": answer["question_id"],
            "question": answer["question_text"], "values": answer["values"],
            "answered_at": answer["answered_at"],
        })
    return {"instruction": request.instruction, "user_answers": answers}


def merge_artifact_index(existing: list, refs: Iterable[ArtifactRef]) -> list[dict]:
    """Append registered artifact navigation once, preserving original order."""
    entries = {}
    for value in existing:
        entry = ResearchArtifactEntry.model_validate(value)
        entries[entry.artifact_id] = entry.model_dump(mode="json", exclude_none=True)
    for ref in refs:
        if ref.id not in entries:
            entries[ref.id] = ResearchArtifactEntry.from_ref(ref).model_dump(mode="json", exclude_none=True)
    return list(entries.values())


def artifact_index_context(
    request: AgentRequest, state: AgentState, *,
    groups: list[ResearchIndexGroup] | None = None, index_artifact_id: str | None = None,
) -> ContextSection:
    """Render the complete authorized directory without copying artifact contents."""
    entries = merge_artifact_index([], request.input_artifacts)
    by_id = {entry["artifact_id"]: entry for entry in entries}
    for entry in merge_artifact_index(state.memory.get("artifact_index", []), []):
        if entry["artifact_id"] not in by_id:
            entries.append(entry)
            by_id[entry["artifact_id"]] = entry
    value = {"artifacts": entries}
    if groups is not None:
        value["index_artifact_id"] = index_artifact_id
        value["groups"] = [
            {"key": group.key, "title": group.title,
             "artifact_ids": [entry.artifact_id for entry in group.artifacts]}
            for group in groups
        ]
        for group in groups:
            for entry in group.artifacts:
                if entry.execution_status is not None:
                    by_id[entry.artifact_id]["execution_status"] = entry.execution_status.value
    return ContextSection(
        name="artifact_index", content=json.dumps(value, ensure_ascii=False),
        priority=95, required=True,
    )


def read_work_feedback_source(
    feedback: WorkFeedback, reader: RegisteredArtifactReader,
) -> tuple[ArtifactRef, WorkRecord]:
    """Validate and load the WorkRecord referenced by shared feedback."""
    record_ref = reader.resolve_ref(feedback.work_record_artifact_id)
    if (record_ref is None or record_ref.kind != "work_record"
            or record_ref.session_id != feedback.session_id):
        raise ArtifactReadError("work feedback has no authorized work record for this session")
    record = read_artifact_json(reader, record_ref.id, WorkRecord)
    if (record.run_id, record.session_id, record.work_request_id) != (
            feedback.run_id, feedback.session_id, feedback.work_request_id):
        raise ArtifactReadError("work record does not belong to this feedback")
    return record_ref, record


def _generic_work_feedback_context(ref: ArtifactRef, feedback: WorkFeedback) -> ContextMaterial:
    """Keep non-Scientific callers on a bounded, source-only material view."""
    def render(chars: int) -> str:
        window = slice_text_lines(feedback.report, max_chars=chars)
        return json.dumps({
            "artifact_id": ref.id, "kind": ref.kind,
            "content": {
                "work_request_id": feedback.work_request_id,
                "work_record_artifact_id": feedback.work_record_artifact_id,
                "report": window["content"],
                "report_truncated": window["truncated"],
                "report_omitted": not window["content"],
            },
        }, ensure_ascii=False)
    return ContextMaterial(name=f"material_{ref.id}", render=render, weight=16, priority=100)


def request_materials_context(request: AgentRequest) -> list[ContextSection | ContextMaterial]:
    reader = RegisteredArtifactReader(request.input_artifacts, run_id=request.run_id)
    required = set(request.resume_artifact_ids)
    sections: list[ContextSection | ContextMaterial] = []
    for ref in request.input_artifacts:
        if ref.kind == "answer":
            continue
        if ref.id not in required and ref.kind not in {"acceptance_requirements", "conclusion_requirements"}:
            continue
        value = read_request_material(request, ref, reader=reader)
        if ref.kind == "work_feedback":
            feedback = WorkFeedback.model_validate(value)
            read_work_feedback_source(feedback, reader)
            sections.append(_generic_work_feedback_context(ref, feedback))
            continue
        sections.append(ContextSection(
            name=f"material_{ref.id}", content=json.dumps({"artifact_id": ref.id, "kind": ref.kind, "content": value}),
            priority=100, required=True,
        ))
    return sections


def request_dataset_refs(request: AgentRequest) -> list[DatasetRef]:
    reader = RegisteredArtifactReader(request.input_artifacts, run_id=request.run_id)
    refs = {}
    for artifact in request.input_artifacts:
        if artifact.kind != "dataset_catalog":
            continue
        content = read_artifact_json(reader, artifact.id)
        for item in content["datasets"]:
            ref = DatasetRef.model_validate(item)
            if ref.dataset_id in refs and refs[ref.dataset_id] != ref:
                raise ArtifactReadError("dataset remapped during invocation")
            refs[ref.dataset_id] = ref
    return list(refs.values())
