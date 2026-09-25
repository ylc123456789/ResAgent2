"""Shared output facts and read-only access to registered ArtifactRefs."""

from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse
from urllib.request import url2pathname

from resagent2_contracts import ArtifactCandidate, ArtifactRef, RunId, SessionId, WorkspaceGrant, SYSTEM_ARTIFACT_KINDS

from .workspace import WorkspaceBoundary, WorkspacePermissionError

from .text import MAX_READ_CHARS, slice_text_lines, wrap_text_lines


def research_artifacts(artifacts: list[ArtifactRef]) -> list[ArtifactRef]:
    """Select research materials once for directory construction and its readers."""
    excluded = (SYSTEM_ARTIFACT_KINDS - {"work_record", "answer"}) | {"observation_trace"}
    return [ref for ref in artifacts if ref.kind not in excluded]


class ArtifactCandidateError(ValueError):
    """A submitted output can be corrected by its author, without changing policy."""

    def __init__(self, code: str, message: str, *, subject: str | None = None):
        self.code = code
        self.subject = subject
        super().__init__(message)


def check_output_names(artifacts) -> None:
    """Logical names must identify exactly one output."""
    names = set()
    for item in artifacts:
        if item.output_name is not None:
            if item.output_name in names:
                raise ArtifactCandidateError(
                    "duplicate_output_name", f"Use a unique output_name for each output artifact: {item.output_name}",
                    subject=item.output_name,
                )
            names.add(item.output_name)


def resolve_artifact_source(
    path: str, *, grant: WorkspaceGrant | None, output_dir: str | None = None,
) -> tuple[str, Path, Path]:
    """Resolve one file under the same authorized roots at finish and registration.

    Missing or ambiguous submitted files are correctable. Authorization errors
    and other IO failures propagate; they are never converted into acceptance.
    """
    if grant is None and output_dir is None:
        raise WorkspacePermissionError("workspace-file ArtifactCandidate requires a workspace grant")
    roots = []
    if grant is not None:
        roots.append(("workspace", Path(grant.root).resolve(strict=True)))
    if output_dir is not None:
        roots.append(("output_dir", Path(output_dir).resolve()))
    matches = [(label, root, (root / path).resolve())
               for label, root in roots if (root / path).is_file()]
    if len(matches) != 1:
        raise ArtifactCandidateError(
            "artifact_path_missing" if not matches else "artifact_path_ambiguous",
            f"artifact path is missing or ambiguous across authorized roots: {path}",
            subject=path,
        )
    label, root, source = matches[0]
    if not source.is_file() or not source.is_relative_to(root):
        raise WorkspacePermissionError("artifact path is outside workspace or not a file")
    if label == "workspace":
        source = WorkspaceBoundary(grant).resolve_read_file(path)
    return label, root, source


def check_task_output_artifacts(artifacts, *, grant, output_dir=None) -> None:
    """Check submitted names and file facts; registration supplies provenance."""
    check_output_names(artifacts)
    for item in artifacts:
        if isinstance(item, ArtifactCandidate) and item.content is None:
            resolve_artifact_source(item.path, grant=grant, output_dir=output_dir)


class ArtifactReadError(ValueError):
    """Raised when a registered ArtifactRef cannot be verified and read."""


class RegisteredArtifactReader:
    """Read explicitly granted or live-authorized refs within one Run only."""

    def __init__(
        self,
        artifacts: list[ArtifactRef],
        *,
        run_id: RunId,
        resolve: Callable[[str], ArtifactRef | None] | None = None,
    ) -> None:
        self._run_id = run_id
        self._artifacts = {artifact.id: artifact for artifact in artifacts}
        self._resolve = resolve

    def resolve_ref(self, artifact_id: str) -> ArtifactRef | None:
        """Return an authorized, Run-scoped Ref without reading its file."""
        artifact = self._artifacts.get(artifact_id)
        if artifact is None and self._resolve is not None:
            artifact = self._resolve(artifact_id)
        # Check provenance before touching a path, even if a resolver is buggy.
        if (
            artifact is None
            or artifact.id != artifact_id
            or artifact.run_id != self._run_id
        ):
            return None
        return artifact

    def read_text(
        self,
        artifact_id: str,
        *,
        max_chars: int = MAX_READ_CHARS,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> dict:
        """Verify the entire frozen file before returning an optional text window."""
        artifact = self.resolve_ref(artifact_id)
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
        return {
            "artifact_id": artifact.id,
            "kind": artifact.kind,
            "summary": artifact.summary,
            **slice_text_lines(
                text, start_line=start_line, end_line=end_line, max_chars=max_chars,
            ),
        }


REPORT_LINE_CHARS = 1_000


def _wrap_lines(text: str) -> str:
    """Insert line breaks without dropping existing content or whitespace."""
    return wrap_text_lines(text, max_chars=REPORT_LINE_CHARS)


def build_module_report(details: dict[str, str | list[str]]) -> ArtifactCandidate:
    """Package caller-selected prose, never a full result or execution state.

    The caller selects the relevant answer, source paths or residual risks.
    This is a readable projection; the original payload keeps exact values.
    Bounded physical lines make long prose reachable through read_artifact's
    existing line ranges. Registration supplies identity and provenance.
    """
    sections = [
        "# Module report",
        "Module-provided explanation, not independently verified or measured "
        "evidence. Consult original evidence for factual claims.",
    ]
    for name, value in details.items():
        body = (
            _wrap_lines(value)
            if isinstance(value, str)
            else "\n".join("- " + _wrap_lines(item).replace("\n", "\n  ") for item in value)
        )
        sections.append(f"## {name}\n\n{body}")
    return ArtifactCandidate(
        kind="module_report",
        path="module_report.md",
        media_type="text/markdown",
        summary="Module-provided explanation and limitations, not measured evidence",
        content="\n\n".join(sections) + "\n",
    )


def media_type_for(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


class ArtifactRegistrationPort(Protocol):
    """Scientific Tool seam for freezing results via the ResAgent Registry.

    The composition root adapts the orchestrator ArtifactRegistry to this shape;
    capabilities must not import the orchestrator.
    """

    def register_scientific(
        self,
        candidate: ArtifactCandidate,
        *,
        run_id: RunId,
        session_id: SessionId,
    ) -> ArtifactRef:
        """Freeze one candidate with session provenance and return its Ref."""

    def resolve(self, artifact_id: str, *, run_id: RunId) -> ArtifactRef | None:
        """Return a live-authorized artifact of this Run, or ``None``.

        This lets the Scientific Agent's ``read_artifact`` see an artifact
        (e.g. a literature search) registered earlier in the same turn.
        An artifact registered for another Run must never be returned.
        """
