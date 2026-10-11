"""Complete task facts and direct artifact navigation shared by all Agents."""

from __future__ import annotations

import json
from resagent2_contracts import AgentRequest, ArtifactIndexEntry, ArtifactRef, DatasetRef
from resagent2_runtime import AgentState, ContextSection
from .artifacts import ArtifactReadError, RegisteredArtifactReader


def read_artifact_json(reader: RegisteredArtifactReader, artifact_id: str, model=None):
    window = reader.read_text(artifact_id, max_chars=16_000_000)
    if window.get("truncated"):
        raise ArtifactReadError("structured artifact exceeds the reading limit")
    value = json.loads(window["content"])
    return model.model_validate(value) if model is not None else value


def request_task_context(request: AgentRequest) -> dict:
    """Initial facts and complete business dialogue; no operation approvals."""
    value = request.model_dump(mode="json", exclude_none=True, include={
        "run_id", "task_id", "attempt_number", "instruction", "task_context",
        "permissions", "confirm_commands", "workspace", "workspace_id",
        "workspace_spec", "environment_spec", "output_dir", "budget",
    })
    value["user_answers"] = [answer.model_dump(mode="json", exclude_none=True)
                              for answer in request.user_answers]
    return value


def artifact_index_context(
    request: AgentRequest, state: AgentState, *, reader: RegisteredArtifactReader | None = None,
) -> ContextSection:
    """Expand every authorized artifact once, under its actual producer location.

    WorkRequest and Task headings are existing navigation keys, not stored index
    artifacts. Reused material keeps its original location and is cross-referenced.
    """
    refs = {ref.id: ref for ref in request.input_artifacts}
    for artifact_id in state.authorized_artifact_ids:
        if artifact_id in refs:
            continue
        ref = reader.resolve_ref(artifact_id) if reader is not None else None
        if ref is None:
            raise ArtifactReadError(f"authorized artifact is unavailable: {artifact_id}")
        refs[artifact_id] = ref
    entries = {key: ArtifactIndexEntry.from_ref(ref).model_dump(mode="json", exclude_none=True)
               for key, ref in refs.items()}
    placed = set()
    works = []
    for work in request.work_results:
        tasks = []
        for task in work.tasks:
            owned = [ref.id for ref in refs.values() if ref.task_id == task.task_id]
            actual = [entries[key] for key in owned if key not in placed]
            placed.update(owned)
            task_value = task.model_dump(mode="json", exclude_none=True, exclude={
                "error": {"details"}, "warnings": {"__all__": {"details"}},
                "attempts": {"__all__": {
                    "error": {"details"}, "reception_error": {"details"},
                    "warnings": {"__all__": {"details"}},
                }},
            })
            task_value["artifacts"] = actual
            tasks.append(task_value)
        work_value = work.model_dump(mode="json", exclude_none=True,
                                     exclude={"tasks": True, "artifacts": True, "error": {"details"}})
        work_value["tasks"] = tasks
        own_ids = {entry["artifact_id"] for task in tasks for entry in task["artifacts"]}
        work_value["referenced_artifact_ids"] = [ref.id for ref in work.artifacts if ref.id not in own_ids]
        if not tasks:
            work_value["note"] = "No tasks were created."
        works.append(work_value)
    categories = {"literature": [], "web": [], "other": []}
    for key, entry in entries.items():
        if key in placed:
            continue
        kind = entry["kind"]
        category = "literature" if kind in {"literature_paper", "literature_pdf", "literature_fulltext"} else (
            "web" if kind == "web_page" else "other")
        categories[category].append(entry)
    value = {"artifact_count": len(entries), "materials": categories, "work_requests": works}
    return ContextSection(name="artifact_index", content=json.dumps(value, ensure_ascii=False))


def request_dataset_refs(request: AgentRequest) -> list[DatasetRef]:
    """Resolve the declared catalog without projecting material bodies to context."""
    values = request.task_context.get("dataset_refs")
    if values is not None:
        return [DatasetRef.model_validate(value) for value in values]
    reader = RegisteredArtifactReader(request.input_artifacts, run_id=request.run_id)
    refs = {}
    for artifact in request.input_artifacts:
        if artifact.kind != "dataset_catalog":
            continue
        for item in read_artifact_json(reader, artifact.id)["datasets"]:
            ref = DatasetRef.model_validate(item)
            if ref.dataset_id in refs and refs[ref.dataset_id] != ref:
                raise ArtifactReadError("dataset remapped during invocation")
            refs[ref.dataset_id] = ref
    return list(refs.values())
