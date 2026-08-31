"""ArtifactCandidate validation and immutable registration."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from resagent2_contracts import (
    AgentOwner,
    ArtifactCandidate,
    ArtifactImport,
    ArtifactRef,
    RunId,
    SessionId,
    TaskId,
    WorkspaceGrant,
)


class ArtifactRegistrationError(ValueError):
    """Raised when a candidate cannot become a provenance-safe ArtifactRef."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_import_uri(uri: str) -> Path:
    """Resolve a caller-supplied import URI to a local file path."""
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return Path(url2pathname(parsed.path)).expanduser().resolve()
    if parsed.scheme == "":
        return Path(parsed.path).expanduser().resolve()
    raise ArtifactRegistrationError(f"unsupported import uri scheme: {parsed.scheme!r}")


class ArtifactRegistry:
    """Copy validated workspace files into an immutable run artifact directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def register(
        self,
        candidate: ArtifactCandidate,
        *,
        grant: WorkspaceGrant | None,
        producer: AgentOwner,
        run_id: RunId,
        task_id: TaskId,
        attempt_number: int,
        index: int,
        existing_ids: set[str],
    ) -> ArtifactRef:
        suffix = task_id.removeprefix("task_")
        artifact_id = f"artifact_{suffix}_{attempt_number}_{index}"
        if artifact_id in existing_ids:
            raise ArtifactRegistrationError(f"artifact id already exists: {artifact_id}")

        # Validate candidate/grant/source/path BEFORE creating any directory, so
        # a rejected candidate cannot leave a residue directory that would break
        # a later retry of the same id (ADR-0011 §5.1).
        source: Path | None = None
        if candidate.content is None:
            if grant is None:
                raise ArtifactRegistrationError(
                    "workspace-file ArtifactCandidate requires a workspace grant"
                )
            workspace = Path(grant.root).resolve(strict=True)
            source = (workspace / candidate.path).resolve(strict=True)
            if not source.is_file() or not source.is_relative_to(workspace):
                raise ArtifactRegistrationError(
                    "artifact path is outside workspace or not a file"
                )
            if grant.allowed_paths and not any(
                source.is_relative_to((workspace / path).resolve())
                for path in grant.allowed_paths
            ):
                raise ArtifactRegistrationError("artifact path is outside allowed_paths")
            if any(
                source.is_relative_to((workspace / path).resolve())
                for path in grant.denied_paths
            ):
                raise ArtifactRegistrationError("artifact path is inside denied_paths")

        destination_dir = self.root / run_id / artifact_id
        destination_dir.mkdir(parents=True, exist_ok=False)
        destination = destination_dir / (
            Path(candidate.path).name if candidate.content is not None else source.name
        )
        temporary: Path | None = None
        try:
            if candidate.content is not None:
                with tempfile.NamedTemporaryFile(
                    dir=destination_dir, delete=False
                ) as handle:
                    temporary = Path(handle.name)
                    handle.write(candidate.content.encode("utf-8"))
            else:
                with tempfile.NamedTemporaryFile(
                    dir=destination_dir, delete=False
                ) as handle:
                    temporary = Path(handle.name)
                shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
            digest = _sha256(destination)
        except Exception:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            shutil.rmtree(destination_dir, ignore_errors=True)
            raise

        return ArtifactRef(
            id=artifact_id,
            kind=candidate.kind,
            producer=producer,
            run_id=run_id,
            task_id=task_id,
            attempt_number=attempt_number,
            uri=destination.as_uri(),
            sha256=digest,
            media_type=candidate.media_type,
            summary=candidate.summary,
            metadata=candidate.metadata,
        )

    def register_import(
        self,
        spec: ArtifactImport,
        *,
        run_id: RunId,
    ) -> ArtifactRef:
        """Freeze a caller-supplied local input into an orchestrator Artifact.

        The controller validates the local URI, copies it into the run's frozen
        artifact directory, verifies the optional hash and returns an
        ``orchestrator/import`` ArtifactRef (ADR-0011 §4). Callers must never
        pass a self-built ArtifactRef as input.
        """
        source = _resolve_import_uri(spec.uri)
        if not source.is_file():
            raise ArtifactRegistrationError(
                f"import uri is not a readable file: {spec.uri}"
            )
        digest = _sha256(source)
        if spec.expected_sha256 is not None and spec.expected_sha256 != digest:
            raise ArtifactRegistrationError(
                f"import sha256 mismatch for {spec.uri}: "
                f"expected {spec.expected_sha256}, got {digest}"
            )
        artifact_id = f"artifact_import_{digest[:16]}"
        destination_dir = self.root / run_id / artifact_id
        destination = destination_dir / source.name
        if not destination.exists():
            destination_dir.mkdir(parents=True, exist_ok=True)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=destination_dir, delete=False
                ) as handle:
                    temporary = Path(handle.name)
                shutil.copyfile(source, temporary)
                os.replace(temporary, destination)
            except Exception:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                shutil.rmtree(destination_dir, ignore_errors=True)
                raise
        return ArtifactRef(
            id=artifact_id,
            kind=spec.kind,
            producer=AgentOwner.ORCHESTRATOR,
            run_id=run_id,
            uri=destination.as_uri(),
            sha256=digest,
            media_type=spec.media_type,
            summary=spec.summary,
            metadata={"source_type": "import"},
        )

    def register_scientific(
        self,
        candidate: ArtifactCandidate,
        *,
        run_id: RunId,
        session_id: SessionId,
    ) -> ArtifactRef:
        """Freeze one session-bound Scientific artifact (e.g. literature search).

        Unlike task artifacts, the content is not a workspace file: the
        Scientific Tool already produced a normalized result and carries it in
        ``candidate.metadata``. The content is serialized, hashed and written
        atomically. The id is content-derived, so registering the same content
        again is idempotent.
        """
        if candidate.kind != "literature_search":
            raise ArtifactRegistrationError(
                f"unsupported scientific artifact kind: {candidate.kind}"
            )
        encoded = json.dumps(
            candidate.metadata, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        artifact_id = f"artifact_sci_{digest[:16]}"

        destination_dir = self.root / run_id / artifact_id
        destination = destination_dir / candidate.path
        destination_dir.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=destination_dir, delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(encoded)
                os.replace(temporary, destination)
            except Exception:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                raise

        return ArtifactRef(
            id=artifact_id,
            kind=candidate.kind,
            producer=AgentOwner.SCIENTIFIC,
            run_id=run_id,
            session_id=session_id,
            uri=destination.as_uri(),
            sha256=digest,
            media_type=candidate.media_type,
            summary=candidate.summary,
            metadata=candidate.metadata,
        )

    def register_final_report(
        self,
        candidate: ArtifactCandidate,
        content: str,
        *,
        run_id: RunId,
    ) -> ArtifactRef:
        """Atomically freeze one deterministic orchestrator final report.

        The stable id and content check make a retry safe if the file was
        written immediately before a process crash but the Run was not saved.
        """
        if (
            candidate.kind != "final_report"
            or candidate.media_type != "text/markdown"
            or candidate.metadata.get("source_type") != "final_report"
            or candidate.path != "final_report.md"
        ):
            raise ArtifactRegistrationError("invalid final report candidate")

        artifact_id = "artifact_final_report"
        destination_dir = self.root / run_id / artifact_id
        destination = destination_dir / candidate.path
        encoded = content.encode("utf-8")
        expected_digest = hashlib.sha256(encoded).hexdigest()

        destination_dir.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if not destination.is_file() or _sha256(destination) != expected_digest:
                raise ArtifactRegistrationError("final report path already has other content")
        else:
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=destination_dir, delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(encoded)
                os.replace(temporary, destination)
            except Exception:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                raise

        artifact = ArtifactRef(
            id=artifact_id,
            kind=candidate.kind,
            producer=AgentOwner.ORCHESTRATOR,
            run_id=run_id,
            uri=destination.as_uri(),
            sha256=expected_digest,
            media_type=candidate.media_type,
            summary=candidate.summary,
            metadata=candidate.metadata,
        )
        return artifact
