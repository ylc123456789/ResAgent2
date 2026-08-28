"""ArtifactCandidate validation and immutable registration."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    ArtifactCandidate,
    ArtifactRef,
    RunId,
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
        if grant is None:
            raise ArtifactRegistrationError("ArtifactCandidate requires a workspace grant")
        workspace = Path(grant.root).resolve(strict=True)
        source = (workspace / candidate.path).resolve(strict=True)
        if not source.is_file() or not source.is_relative_to(workspace):
            raise ArtifactRegistrationError("artifact path is outside workspace or not a file")
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

        suffix = task_id.removeprefix("task_")
        artifact_id = f"artifact_{suffix}_{attempt_number}_{index}"
        if artifact_id in existing_ids:
            raise ArtifactRegistrationError(f"artifact id already exists: {artifact_id}")

        destination_dir = self.root / run_id / artifact_id
        destination_dir.mkdir(parents=True, exist_ok=False)
        destination = destination_dir / source.name
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination_dir, delete=False) as handle:
                temporary = Path(handle.name)
            shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
            digest = _sha256(destination)
        except Exception:
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
