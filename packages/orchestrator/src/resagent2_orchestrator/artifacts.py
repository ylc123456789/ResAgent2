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
        self.root = Path(root)
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
