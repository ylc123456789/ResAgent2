"""Immutable artifact registration with durable UUID allocation before freezing."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from resagent2_components.artifacts import (
    ArtifactCandidateError, ArtifactReadError, RegisteredArtifactReader, missing_required_artifacts,
    resolve_artifact_source,
)
from resagent2_contracts import (
    AgentOwner, ArtifactCandidate, ArtifactImport, ArtifactRef,
    SYSTEM_ARTIFACT_PROVENANCE, SCIENTIFIC_ARTIFACT_KINDS, new_artifact_id,
)
from .models import PendingRegistration
from .store import RunStore


class ArtifactRegistrationError(ValueError):
    """A candidate cannot become a provenance-safe immutable ArtifactRef."""


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _resolve_import_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme == "file" and parsed.netloc in {"", "localhost"}:
        return Path(url2pathname(parsed.path)).expanduser().resolve()
    if parsed.scheme == "":
        return Path(parsed.path).expanduser().resolve()
    raise ArtifactRegistrationError(f"unsupported import uri scheme: {parsed.scheme!r}")


class ArtifactRegistry:
    """Validate sources and freeze them using one content-independent identity."""

    def __init__(self, root: str | Path, store: RunStore | None = None) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = store

    def missing_required_artifacts(self, required_names, *, run_id, artifacts):
        reader = RegisteredArtifactReader(list(artifacts.values()), run_id=run_id)
        return missing_required_artifacts(required_names, artifact_ids=artifacts, reader=reader)

    def _run(self, run_id, run):
        if run is not None:
            if run.run_id != run_id:
                raise ArtifactRegistrationError("registration Run mismatch")
            return run
        return self.store.load(run_id) if self.store is not None else None

    def _save(self, run):
        if run is not None:
            if self.store is not None:
                self.store.save(run)

    def _freeze(self, candidate, *, run_id, producer, operation_key=None, run=None,
                source=None, encoded=None, metadata=None, task_id=None,
                attempt_number=None, session_id=None, await_receipt=False):
        run = self._run(run_id, run)
        if run is not None and operation_key is None:
            raise ArtifactRegistrationError("durable registration requires its logical operation key")
        facts = dict(metadata if metadata is not None else candidate.metadata)
        pending = run.pending_registrations.get(operation_key) if run is not None else None
        if pending is not None:
            ref = pending.ref
            if pending.candidate_path != candidate.path:
                raise ArtifactRegistrationError("registration recovery path changed")
            expected = (candidate.kind, producer, task_id, attempt_number, session_id,
                        candidate.media_type, candidate.summary, candidate.output_name, facts)
            actual = (ref.kind, ref.producer, ref.task_id, ref.attempt_number, ref.session_id,
                      ref.media_type, ref.summary, ref.output_name, ref.metadata)
            if expected != actual:
                raise ArtifactRegistrationError("registration recovery facts changed")
            if encoded is not None and hashlib.sha256(encoded).hexdigest() != ref.sha256:
                raise ArtifactRegistrationError("registration slot has different content")
            destination = Path(url2pathname(urlparse(ref.uri).path))
            if destination.is_file():
                if _sha256(destination) != ref.sha256:
                    raise ArtifactRegistrationError("frozen artifact hash changed")
                if run.artifacts.get(ref.id) not in (None, ref):
                    raise ArtifactRegistrationError("artifact identity conflicts with Run registration")
                run.artifacts[ref.id] = ref
                self._save(run)
                return ref
            # Regenerated imports may have a new temporary path; their bytes
            # must still match the snapshot recorded before the interruption.
            if source is None and pending.source_path and encoded is None:
                source = Path(pending.source_path)
            digest = hashlib.sha256(encoded).hexdigest() if encoded is not None else _sha256(source)
            if digest != ref.sha256:
                raise ArtifactRegistrationError("source changed before interrupted registration froze")
        else:
            digest = hashlib.sha256(encoded).hexdigest() if encoded is not None else _sha256(source)
            artifact_id = new_artifact_id()
            filename = Path(candidate.path).name if encoded is not None else source.name
            destination = self.root / run_id / artifact_id / filename
            ref = ArtifactRef(id=artifact_id, kind=candidate.kind, producer=producer, run_id=run_id,
                              task_id=task_id, attempt_number=attempt_number, session_id=session_id,
                              uri=destination.as_uri(), sha256=digest, media_type=candidate.media_type,
                              summary=candidate.summary, output_name=candidate.output_name, metadata=facts)
            if run is not None:
                run.pending_registrations[operation_key] = PendingRegistration(
                    ref=ref, candidate_path=candidate.path,
                    source_path=str(source) if source is not None else None,
                    await_receipt=await_receipt,
                )
                self._save(run)
        destination.parent.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(dir=destination.parent.parent, prefix=f".{ref.id}."))
        try:
            staged_file = staging / destination.name
            if encoded is not None:
                staged_file.write_bytes(encoded)
            else:
                shutil.copyfile(source, staged_file)
            if _sha256(staged_file) != ref.sha256:
                raise ArtifactRegistrationError("artifact source changed while freezing")
            os.replace(staging, destination.parent)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        if run is not None:
            run.artifacts[ref.id] = ref
            self._save(run)
        return ref

    def acknowledge_registration(self, operation_key, *, run_id, run=None):
        """Commit caller association only after its authoritative receipt is saved."""
        run = self._run(run_id, run)
        if run is None:
            return
        pending = run.pending_registrations.get(operation_key)
        if pending is None:
            return
        if run.artifacts.get(pending.ref.id) != pending.ref:
            raise ArtifactRegistrationError("cannot acknowledge an uncommitted artifact")
        del run.pending_registrations[operation_key]
        self._save(run)

    def register(self, candidate, *, grant, producer, run_id, task_id, attempt_number,
                 index, output_dir=None, operation_key=None, run=None):
        key = operation_key or f"task:{task_id}:attempt:{attempt_number}:output:{index}"
        run = self._run(run_id, run)
        pending = run.pending_registrations.get(key) if run is not None else None
        metadata = dict(candidate.metadata)
        metadata.pop("source_path", None)
        metadata.pop("source_root", None)
        source = None
        if candidate.content is None:
            if pending is not None:
                source = Path(pending.source_path)
                metadata.update({key: pending.ref.metadata[key] for key in ("source_path", "source_root")})
            else:
                try:
                    label, workspace, source = resolve_artifact_source(candidate.path, grant=grant, output_dir=output_dir)
                except (ArtifactCandidateError, OSError) as error:
                    raise ArtifactRegistrationError(str(error)) from error
                metadata.update(source_path=source.relative_to(workspace).as_posix(), source_root=label)
        elif candidate.kind == "execution_record" and pending is None and run is not None:
            # A paused invocation can hand over the same cumulative command
            # snapshot again at completion. Reuse only equal frozen bytes from
            # the same producing Attempt; paths never establish identity.
            digest = hashlib.sha256(candidate.content.encode("utf-8")).hexdigest()
            expected = (candidate.kind, producer, run_id, task_id, attempt_number, None, digest,
                        candidate.media_type, candidate.output_name, metadata)
            for ref in run.artifacts.values():
                if (ref.kind, ref.producer, ref.run_id, ref.task_id, ref.attempt_number, ref.session_id, ref.sha256,
                        ref.media_type, ref.output_name, ref.metadata) != expected:
                    continue
                try:
                    RegisteredArtifactReader([ref], run_id=run_id).verify(ref.id)
                except ArtifactReadError as error:
                    raise ArtifactRegistrationError(str(error)) from error
                return ref
        return self._freeze(candidate, run_id=run_id, producer=producer, operation_key=key, run=run,
                            source=source, encoded=candidate.content.encode("utf-8") if candidate.content is not None else None,
                            metadata=metadata, task_id=task_id, attempt_number=attempt_number)

    def register_import(self, spec: ArtifactImport, *, run_id, operation_key=None, run=None):
        source = _resolve_import_uri(spec.uri)
        pending_run = self._run(run_id, run)
        pending = pending_run.pending_registrations.get(operation_key) if pending_run is not None else None
        if pending is None and not source.is_file():
            raise ArtifactRegistrationError(f"import uri is not a readable file: {spec.uri}")
        digest = pending.ref.sha256 if pending is not None else _sha256(source)
        if spec.expected_sha256 is not None and spec.expected_sha256 != digest:
            raise ArtifactRegistrationError(f"import sha256 mismatch for {spec.uri}")
        metadata = {**spec.metadata, "source_type": "import"}
        # Import reuse is content and provenance equality, independent of UUID generation.
        if pending is None and pending_run is not None:
            for ref in pending_run.artifacts.values():
                if (ref.kind, ref.sha256, ref.media_type, ref.metadata) == (spec.kind, digest, spec.media_type, metadata):
                    try:
                        RegisteredArtifactReader([ref], run_id=run_id).verify(ref.id)
                    except ArtifactReadError as error:
                        raise ArtifactRegistrationError(str(error)) from error
                    return ref
        candidate = ArtifactCandidate(kind=spec.kind, path=source.name, media_type=spec.media_type,
                                      summary=spec.summary, metadata=metadata)
        return self._freeze(candidate, run_id=run_id, producer=AgentOwner.ORCHESTRATOR,
                            operation_key=operation_key, run=pending_run, source=source, metadata=metadata)

    def register_scientific(self, candidate, *, run_id, session_id, content_bytes=None,
                            operation_key=None, run=None, await_receipt=False):
        if candidate.kind not in SCIENTIFIC_ARTIFACT_KINDS:
            raise ArtifactRegistrationError(f"unsupported scientific artifact kind: {candidate.kind}")
        if content_bytes is not None and candidate.content is not None:
            raise ArtifactRegistrationError("supply text or binary content, not both")
        encoded = content_bytes if content_bytes is not None else (
            candidate.content if candidate.content is not None else json.dumps(candidate.metadata, sort_keys=True, ensure_ascii=False, indent=2)
        ).encode("utf-8")
        return self._freeze(candidate, run_id=run_id, producer=AgentOwner.SCIENTIFIC,
                            operation_key=operation_key, run=run, encoded=encoded,
                            session_id=session_id, await_receipt=await_receipt)

    def register_system_artifact(self, candidate, *, run_id, source_type, session_id=None,
                                 task_id=None, attempt_number=None, operation_key=None, run=None):
        if candidate.kind not in SYSTEM_ARTIFACT_PROVENANCE:
            raise ArtifactRegistrationError(f"unsupported system artifact kind: {candidate.kind}")
        encoded = (candidate.content if candidate.content is not None else json.dumps(
            candidate.metadata, sort_keys=True, ensure_ascii=False, indent=2)).encode("utf-8")
        return self._freeze(candidate, run_id=run_id, producer=AgentOwner.ORCHESTRATOR,
                            operation_key=operation_key, run=run, encoded=encoded,
                            metadata={**candidate.metadata, "source_type": source_type},
                            session_id=session_id, task_id=task_id, attempt_number=attempt_number)

    def register_final_report(self, candidate, content, *, run_id, run=None):
        if (candidate.kind, candidate.media_type, candidate.metadata.get("source_type"), candidate.path) != (
                "final_report", "text/markdown", "final_report", "final_report.md"):
            raise ArtifactRegistrationError("invalid final report candidate")
        run = self._run(run_id, run)
        if run is not None and run.final_report_artifact_id is not None:
            ref = run.artifacts[run.final_report_artifact_id]
            if ref.sha256 != hashlib.sha256(content.encode("utf-8")).hexdigest():
                raise ArtifactRegistrationError("final report differs from committed content")
            RegisteredArtifactReader([ref], run_id=run_id).verify(ref.id)
            return ref
        return self._freeze(candidate, run_id=run_id, producer=AgentOwner.ORCHESTRATOR,
                            operation_key="final_report", run=run, encoded=content.encode("utf-8"))


class ScientificArtifactRegistration:
    """Run authority port; callers maintain their own explicit authorization set."""

    def __init__(self, registry: ArtifactRegistry, store: RunStore):
        self._registry = registry
        self._store = store
        self._registry.store = store

    def register_scientific(self, candidate, *, run_id, session_id, operation_key, output_position, content_bytes=None):
        run = self._store.load(run_id)
        ref = self._registry.register_scientific(candidate, run_id=run_id, session_id=session_id,
                                                 content_bytes=content_bytes, operation_key=f"{operation_key}:output:{output_position}",
                                                 run=run, await_receipt=True)
        return ref

    def acknowledge_registration(self, *, run_id, operation_key):
        """Consume allocations only after the Session saved their unique receipt."""
        run = self._store.load(run_id)
        keys = [key for key, pending in run.pending_registrations.items()
                if key.startswith(operation_key + ":output:") and pending.await_receipt]
        for key in keys:
            ref = run.pending_registrations[key].ref
            frozen = Path(url2pathname(urlparse(ref.uri).path))
            if ref.id not in run.artifacts and not frozen.exists():
                # No bytes were frozen before interruption. The receipt records
                # that the call is unknown; this unused allocation grants nothing.
                del run.pending_registrations[key]
                self._store.save(run)
                continue
            RegisteredArtifactReader([ref], run_id=run_id).verify(ref.id)
            if run.artifacts.get(ref.id) != ref:
                raise ArtifactRegistrationError("receipt artifact is not in the trusted registration record")
            if ref.id not in run.scientific_artifact_ids:
                run.scientific_artifact_ids.append(ref.id)
            self._registry.acknowledge_registration(key, run_id=run_id, run=run)

    def recover_registration(self, *, run_id, operation_key):
        """Return truthful committed material refs for an interrupted native receipt."""
        run = self._store.load(run_id)
        refs = []
        for key, pending in sorted(run.pending_registrations.items()):
            if not key.startswith(operation_key + ":output:") or not pending.await_receipt:
                continue
            ref = pending.ref
            frozen = Path(url2pathname(urlparse(ref.uri).path))
            if ref.id not in run.artifacts and not frozen.exists():
                continue
            reader = RegisteredArtifactReader([ref], run_id=run_id)
            reader.verify(ref.id)
            if run.artifacts.get(ref.id) not in (None, ref):
                raise ArtifactRegistrationError("recovery artifact conflicts with registry")
            run.artifacts[ref.id] = ref
            refs.append(ref)
        self._store.save(run)
        return refs

    def resolve(self, artifact_id, *, run_id, session_id):
        if not self._store.exists(run_id):
            return None
        run = self._store.load(run_id)
        ref = run.artifacts.get(artifact_id)
        return ref if ref is not None and ref.session_id == session_id and artifact_id in run.scientific_artifact_ids else None

    def list_artifacts(self, *, run_id, session_id):
        run = self._store.load(run_id)
        return [run.artifacts[key] for key in run.scientific_artifact_ids
                if key in run.artifacts and run.artifacts[key].session_id == session_id]
