"""Shared result reception and immutable structured handoffs."""

import json
import math
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from resagent2_contracts import (
    AgentOwner, ArtifactCandidate, ArtifactRef, TaskAcceptanceSpec,
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


def system_artifact(registry, run, kind, data, *, session_id=None, task_id=None, attempt_number=None,
                    operation_key=None):
    source = SYSTEM_ARTIFACT_PROVENANCE[kind][0]
    content = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
    key = operation_key or f"system:{kind}:{session_id or task_id or run.run_id}:{attempt_number or 0}"
    candidate = ArtifactCandidate(kind=kind, path=f"{kind}.json", media_type="application/json",
                                  summary=kind.replace("_", " "),
                                  content=json.dumps(content, ensure_ascii=False, sort_keys=True))
    ref = registry.register_system_artifact(candidate, run_id=run.run_id, source_type=source,
                                           session_id=session_id, task_id=task_id,
                                           attempt_number=attempt_number, operation_key=key, run=run)
    run.artifacts[ref.id] = ref
    return ref


def receive_artifacts(registry, run, request, result, *, previous_ids=()):
    """Receive material refs without converting control payloads into artifacts."""
    registered = []
    returned_ids = set()
    offset = len(previous_ids)
    names = {run.artifacts[key].output_name for key in previous_ids
             if key in run.artifacts and run.artifacts[key].output_name is not None}
    for index, output in enumerate(result.artifacts):
        key = (f"task:{request.task_id}:attempt:{request.attempt_number}:output:{offset + index + 1}"
               if request.task_id is not None else
               f"session:{result.session.id}:result:{result.session.updated_at.isoformat()}:{index}")
        if isinstance(output, ArtifactRef):
            ref = run.artifacts.get(output.id)
            if ref is None or ref != output or ref.run_id != run.run_id:
                raise ArtifactRegistrationError("returned Ref is not in the trusted registration record")
            if request.task_id is not None:
                owned = ref.task_id == request.task_id and ref.attempt_number == request.attempt_number
            else:
                owned = result.session is not None and ref.session_id == result.session.id
            if not owned or ref.producer != request.agent or output.id in {a.id for a in request.input_artifacts}:
                raise ArtifactRegistrationError("input or foreign artifact cannot be returned as a new output")
            parsed = urlparse(ref.uri)
            if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
                raise ArtifactRegistrationError("returned Ref must use a local file URI")
            read_path = Path(url2pathname(parsed.path))
            if not read_path.is_file() or _sha256(read_path) != ref.sha256:
                raise ArtifactRegistrationError("returned Ref content is missing or corrupt")
        elif request.task_id is None:
            if result.session is None:
                raise ArtifactRegistrationError("Scientific output requires its owning session")
            ref = registry.register_scientific(output, run_id=run.run_id, session_id=result.session.id,
                                               operation_key=key, run=run)
        else:
            ref = registry.register(output, grant=request.workspace, producer=request.agent,
                                    run_id=run.run_id, task_id=request.task_id,
                                    attempt_number=request.attempt_number, index=offset + index + 1,
                                    output_dir=request.output_dir,
                                    operation_key=key, run=run)
        if ref.id in returned_ids:
            raise ArtifactRegistrationError("duplicate returned artifact reference")
        returned_ids.add(ref.id)
        if ref.output_name is not None:
            if ref.output_name in names:
                raise ArtifactRegistrationError("duplicate logical output name")
            names.add(ref.output_name)
        run.artifacts[ref.id] = ref
        registered.append(ref)
    return registered


def control_key(control):
    """Provider IDs are unique only within their original native response."""
    return f"session:{control.source_session_id}:turn:{control.source_turn}:call:{control.source_call_id}"


def receive_control(run, request, result):
    """Commit the already allocated Session payload, idempotently, before acting."""
    signal = result.control
    if signal is None:
        return None
    if result.session is None or signal.source_session_id != result.session.id:
        raise ArtifactRegistrationError("control source session mismatch")
    if request.parent_session_id and signal.source_session_id != request.parent_session_id:
        raise ArtifactRegistrationError("control does not belong to resumed session")
    key = control_key(signal)
    payload = signal.question if signal.action == "ask_user" else signal.work_request
    existing = run.received_controls.get(key)
    if existing is not None and existing != payload.id:
        raise ArtifactRegistrationError("control identity conflicts with previously received call")
    if signal.action == "ask_user":
        question = signal.question
        if (question.run_id, question.task_id, question.attempt_number) != (
                request.run_id, request.task_id, request.attempt_number):
            raise ArtifactRegistrationError("question control scope mismatch")
        if question.session_id != signal.source_session_id:
            raise ArtifactRegistrationError("question session mismatch")
        if existing is None:
            if question.action is None and request.upstream_session_id is not None:
                if question.recipient_session_id != request.upstream_session_id:
                    raise ArtifactRegistrationError("question recipient is not the direct parent")
                run.child_questions[question.id] = question
                run.runtime_notices.append({"event_id": "question:" + question.id,
                    "type": "child_question", "source_session_id": question.session_id,
                    "recipient_session_id": question.recipient_session_id,
                    "question": question.model_dump(mode="json")})
                run.parent_attention = True
            else:
                run.pending_question = question
    else:
        work = signal.work_request
        if request.agent != AgentOwner.SCIENTIFIC:
            raise ArtifactRegistrationError("only Scientific may request work")
        if (work.run_id, work.scientific_session_id) != (run.run_id, signal.source_session_id):
            raise ArtifactRegistrationError("work control scope mismatch")
        if existing is None:
            run.work_requests.append(work)
        run.latest_scientific_assessment = signal.assessment
    run.received_controls[key] = payload.id
    return payload


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
    for item in refs:
        if item.media_type != "application/json":
            continue
        data = read_json(item)
        if isinstance(data, dict):
            numeric.update(key for key, value in data.items() if isinstance(value, (int, float))
                           and not isinstance(value, bool) and math.isfinite(value))
    if not set(spec.required_metric_keys) <= numeric:
        raise ArtifactRegistrationError("required numeric metric keys missing")
