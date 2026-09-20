"""Shared result reception and immutable structured handoffs."""

import json
import math
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from resagent2_contracts import (
    AgentOwner, ArtifactCandidate, ArtifactRef, TaskAcceptanceSpec, VerificationResult,
    SYSTEM_ARTIFACT_PROVENANCE,
)
from .artifacts import ArtifactRegistrationError, _sha256


def read_json(ref: ArtifactRef, model=None):
    parsed = urlparse(ref.uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ArtifactRegistrationError("only registered local artifacts may be read")
    path = Path(url2pathname(parsed.path))
    if not path.is_file() or _sha256(path) != ref.sha256:
        raise ArtifactRegistrationError("registered artifact is missing or its hash changed")
    data = json.loads(path.read_text(encoding="utf-8"))
    return model.model_validate(data) if model else data


def system_artifact(registry, run, kind, data, *, session_id=None, task_id=None, attempt_number=None):
    source = SYSTEM_ARTIFACT_PROVENANCE[kind][0]
    content = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
    ref = registry.register_system_artifact(
        ArtifactCandidate(kind=kind, path=f"{kind}.json", media_type="application/json",
                          summary=kind.replace("_", " "), content=json.dumps(content, ensure_ascii=False, sort_keys=True)),
        run_id=run.run_id, source_type=source, session_id=session_id,
        task_id=task_id, attempt_number=attempt_number,
    )
    run.artifacts[ref.id] = ref
    return ref


def receive_artifacts(registry, run, request, result, *, previous_ids=()):
    """Register incrementally; callers persist partial results even on rejection."""
    registered = []
    returned_ids = set()
    offset = len(previous_ids)
    names = {run.artifacts[key].output_name for key in previous_ids
             if key in run.artifacts and run.artifacts[key].output_name is not None}
    for index, output in enumerate(result.artifacts):
        if isinstance(output, ArtifactRef):
            ref = run.artifacts.get(output.id)
            if ref is None or ref != output or ref.run_id != run.run_id:
                raise ArtifactRegistrationError("returned Ref is not in the trusted registration record")
            if request.task_id is not None:
                owned = ref.task_id == request.task_id and ref.attempt_number == request.attempt_number
            else:
                owned = result.session is not None and ref.session_id == result.session.id
            owned = owned and (ref.producer == request.agent or (
                ref.producer == AgentOwner.ORCHESTRATOR and ref.kind in {"question", "work_request"}))
            if not owned or output.id in {a.id for a in request.input_artifacts}:
                raise ArtifactRegistrationError("input or foreign artifact cannot be returned as a new output")
            parsed = urlparse(ref.uri)
            if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
                raise ArtifactRegistrationError("returned Ref must use a local file URI")
            read_path = Path(url2pathname(parsed.path))
            if not read_path.is_file() or _sha256(read_path) != ref.sha256:
                raise ArtifactRegistrationError("returned Ref content is missing or corrupt")
        elif output.kind in {"question", "work_request"}:
            if output.content is None:
                raise ArtifactRegistrationError("control artifact must provide UTF-8 JSON content")
            if output.kind == "work_request" and request.agent != AgentOwner.SCIENTIFIC:
                raise ArtifactRegistrationError("only Scientific may request work")
            ref = system_artifact(
                registry, run, output.kind, json.loads(output.content),
                session_id=result.session.id if request.task_id is None and result.session else None,
                task_id=request.task_id, attempt_number=request.attempt_number,
            )
        elif request.task_id is None:
            if result.session is None:
                raise ArtifactRegistrationError("Scientific output requires its owning session")
            ref = registry.register_scientific(output, run_id=run.run_id, session_id=result.session.id)
        else:
            ref = registry.register(
                output, grant=request.workspace, producer=request.agent, run_id=run.run_id,
                task_id=request.task_id, attempt_number=request.attempt_number,
                index=offset + index + 1, existing_ids=set(run.artifacts),
                output_dir=request.output_dir,
            )
        if ref.id in returned_ids:
            raise ArtifactRegistrationError("duplicate returned artifact reference")
        returned_ids.add(ref.id)
        if ref.output_name is not None:
            if ref.output_name in names:
                raise ArtifactRegistrationError("duplicate logical output name")
            names.add(ref.output_name)
        run.artifacts[ref.id] = ref
        registered.append(ref)
    control_ref = None
    if result.control is not None:
        signal = result.control
        control_ref = (registered[signal.candidate_index] if signal.candidate_index is not None
                       else next(ref for ref in registered if ref.id == signal.artifact_id))
    return registered, control_ref


def check_acceptance(run, task, attempt, refs):
    if attempt.acceptance_ref != task.acceptance_ref:
        raise ArtifactRegistrationError("Attempt acceptance binding differs from Task")
    if task.acceptance_ref is None:
        return
    ref = task.acceptance_ref
    if run.artifacts.get(ref.id) != ref or ref.task_id != task.id or ref.kind != "acceptance_requirements":
        raise ArtifactRegistrationError("invalid acceptance binding")
    spec = read_json(ref, TaskAcceptanceSpec)
    if any(item.run_id != run.run_id or item.task_id != task.id or item.attempt_number != attempt.number for item in refs):
        raise ArtifactRegistrationError("acceptance can inspect only this Attempt's outputs")
    names = [item.output_name for item in refs if item.output_name is not None]
    if len(names) != len(set(names)) or not set(spec.required_output_names) <= set(names):
        raise ArtifactRegistrationError("required logical outputs missing or ambiguous")
    if not set(spec.required_artifact_kinds) <= {item.kind for item in refs}:
        raise ArtifactRegistrationError("required artifact kinds missing")
    paths = {item.metadata.get("source_path") for item in refs}
    if not set(spec.required_artifact_paths) <= paths:
        raise ArtifactRegistrationError("required artifact paths missing")
    numeric = set()
    executed = False
    for item in refs:
        if item.media_type != "application/json":
            continue
        data = read_json(item)
        if isinstance(data, dict):
            numeric.update(key for key, value in data.items() if isinstance(value, (int, float))
                           and not isinstance(value, bool) and math.isfinite(value))
            if item.kind in {"verification_result", "execution_record"}:
                rows = [VerificationResult.model_validate(row) for row in data.get("results", [])]
                current = item.kind != "verification_result" or data.get("covers_current_workspace") is True
                if item.kind == "execution_record":
                    rows = rows[-1:]
                executed |= current and bool(rows) and all(
                    row.exit_code == 0 and not row.timed_out for row in rows)
    if not set(spec.required_metric_keys) <= numeric:
        raise ArtifactRegistrationError("required numeric metric keys missing")
    if spec.require_successful_execution and not executed:
        raise ArtifactRegistrationError("required successful execution missing")
