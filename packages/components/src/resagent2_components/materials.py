"""Read structured invocation materials through the existing artifact boundary."""

from __future__ import annotations

import json
from resagent2_contracts import (
    AgentRequest, ArtifactRef, DatasetRef, RecordedAnswer, WorkFeedback,
    TaskAcceptanceSpec, ConclusionRequirements,
)
from resagent2_runtime import ContextSection
from .artifacts import ArtifactReadError, RegisteredArtifactReader


def read_artifact_json(reader: RegisteredArtifactReader, artifact_id: str, model=None):
    window = reader.read_text(artifact_id, max_chars=16_000_000)
    if window.get("truncated"):
        raise ArtifactReadError("structured artifact exceeds the reading limit")
    value = json.loads(window["content"])
    return model.model_validate(value) if model is not None else value


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
                or (request.task_id is None and answer.session_id != request.parent_session_id)):
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


def request_materials_context(request: AgentRequest) -> list[ContextSection]:
    reader = RegisteredArtifactReader(request.input_artifacts, run_id=request.run_id)
    required = set(request.resume_artifact_ids)
    sections = []
    for ref in request.input_artifacts:
        if ref.id not in required and ref.kind not in {"acceptance_requirements", "conclusion_requirements"}:
            continue
        value = read_request_material(request, ref, reader=reader)
        if ref.kind == "work_feedback":
            value = {key: value[key] for key in (
                "index_artifact_id", "work_record_artifact_id", "index_changes", "brief",
            )}
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
