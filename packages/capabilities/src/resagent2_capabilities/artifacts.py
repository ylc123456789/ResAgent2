"""Read-only access to immutable, registered ArtifactRefs."""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from resagent2_contracts import ArtifactRef


class ArtifactReadError(ValueError):
    """Raised when a registered ArtifactRef cannot be verified and read."""


class RegisteredArtifactReader:
    """Resolve provided ArtifactRefs (plus an optional live resolver) and verify."""

    def __init__(
        self,
        artifacts: list[ArtifactRef],
        *,
        resolve=None,
    ) -> None:
        self._artifacts = {artifact.id: artifact for artifact in artifacts}
        self._resolve = resolve

    def read_text(self, artifact_id: str, *, max_chars: int = 8_000) -> dict:
        artifact = self._artifacts.get(artifact_id)
        if artifact is None and self._resolve is not None:
            artifact = self._resolve(artifact_id)
        if artifact is None:
            raise ArtifactReadError(f"unknown artifact id: {artifact_id}")
        parsed = urlparse(artifact.uri)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise ArtifactReadError("only local file ArtifactRefs are readable")
        path = Path(url2pathname(parsed.path))
        if not path.is_file():
            raise ArtifactReadError("artifact file is missing")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != artifact.sha256:
            raise ArtifactReadError("artifact sha256 does not match frozen content")
        text = content.decode("utf-8", errors="replace")
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]
        return {
            "artifact_id": artifact.id,
            "kind": artifact.kind,
            "summary": artifact.summary,
            "content": text,
            "truncated": truncated,
        }
