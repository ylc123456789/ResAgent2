"""Resolve a workspace's declared source into a usable repository worktree."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from resagent2_contracts import WorkspaceSourceKind, WorkspaceSpec


class RepoMaterializerError(ValueError):
    """Raised when a repository source is missing, conflicting, or unusable."""


@dataclass(frozen=True, slots=True)
class MaterializedRepo:
    """A resolved repository worktree plus its content identity."""

    repo_path: Path
    commit: str
    source: str


_METADATA_FILENAME = "workspace.json"


def _git_commit(repo_path: Path) -> str:
    """Return the HEAD commit of a repository, or ``""`` when unavailable."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _is_git_repo(path: Path) -> bool:
    return path.is_dir() and bool(_git_commit(path))


def _has_content(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return any(path.iterdir())
    except OSError:
        return True


def _normalize_source(source: str) -> str:
    """Normalize a repo URL or local path for source-identity comparison."""
    value = (source or "").strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if value and "://" not in value:
        value = str(Path(value).expanduser().resolve())
    return value


def _default_metadata_path(workspace: Path) -> Path:
    """Metadata lives one level above the managed repo (workspace.json)."""
    return workspace.parent / _METADATA_FILENAME


def _read_metadata(metadata_path: Path) -> dict | None:
    if not metadata_path.is_file():
        return None
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_metadata(
    metadata_path: Path, source_kind: str, location: str, commit: str
) -> None:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(
            {"source_kind": source_kind, "location": location, "commit": commit}
        ),
        encoding="utf-8",
    )


class RepoMaterializer:
    """Resolve one ``WorkspaceSpec`` into a repository worktree.

    ``workspace`` is the directory that becomes (or already is) the repository
    root. For managed kinds (GIT/COPY/GENERATED) a small ``workspace.json``
    records which source materialized the workspace, so a retry verifies it is
    reusing the same repository rather than silently adopting a leftover from
    another one. LOCAL simply binds the external directory in place and writes
    no metadata (the external path is already the source).
    """

    def __init__(self, *, clone_timeout_seconds: int = 300) -> None:
        self.clone_timeout_seconds = clone_timeout_seconds

    def materialize(
        self,
        *,
        workspace: Path,
        source: WorkspaceSpec,
        metadata_path: Path | None = None,
    ) -> MaterializedRepo:
        """Return the workspace repository for the declared source."""
        workspace = Path(workspace).expanduser().resolve()
        if source.source_kind == WorkspaceSourceKind.GIT:
            if source.location is None:
                raise RepoMaterializerError("GIT source requires a location (URL)")
            meta = metadata_path or _default_metadata_path(workspace)
            return self._clone(workspace, source.location, meta)
        if source.source_kind == WorkspaceSourceKind.COPY:
            if source.location is None:
                raise RepoMaterializerError("COPY source requires a location (path)")
            meta = metadata_path or _default_metadata_path(workspace)
            return self._copy(workspace, source.location, meta)
        if source.source_kind == WorkspaceSourceKind.GENERATED:
            meta = metadata_path or _default_metadata_path(workspace)
            return self._generate(workspace, meta)
        if source.source_kind == WorkspaceSourceKind.LOCAL:
            if source.location is None:
                raise RepoMaterializerError("LOCAL source requires a location (path)")
            return self._bind(source.location)
        raise RepoMaterializerError(
            f"unsupported workspace source kind: {source.source_kind!r}"
        )

    def _verify_source(
        self,
        workspace: Path,
        metadata_path: Path,
        expected_kind: str,
        expected_location: str,
    ) -> None:
        metadata = _read_metadata(metadata_path)
        if metadata is None:
            raise RepoMaterializerError(
                f"workspace at {workspace} has no materialization metadata; "
                f"cannot verify it came from {expected_location!r}"
            )
        actual_kind = metadata.get("source_kind")
        if actual_kind != expected_kind:
            raise RepoMaterializerError(
                f"workspace was materialized as {actual_kind!r}, not {expected_kind!r}"
            )
        if _normalize_source(metadata.get("location", "")) != _normalize_source(
            expected_location
        ):
            raise RepoMaterializerError(
                f"workspace source {metadata.get('location')!r} does not match "
                f"requested {expected_location!r}"
            )

    def _clone(
        self, workspace: Path, repo_url: str, metadata_path: Path
    ) -> MaterializedRepo:
        if _is_git_repo(workspace):
            self._verify_source(
                workspace, metadata_path, WorkspaceSourceKind.GIT.value, repo_url
            )
            return MaterializedRepo(workspace, _git_commit(workspace), "git")
        if _has_content(workspace):
            raise RepoMaterializerError(
                f"cannot clone into non-empty workspace: {workspace}"
            )
        workspace.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch", repo_url, str(workspace)],
            text=True,
            capture_output=True,
            timeout=self.clone_timeout_seconds,
        )
        if result.returncode != 0:
            raise RepoMaterializerError(
                f"git clone failed: {(result.stderr or '').strip() or 'unknown error'}"
            )
        commit = _git_commit(workspace)
        _write_metadata(metadata_path, WorkspaceSourceKind.GIT.value, repo_url, commit)
        return MaterializedRepo(workspace, commit, "git")

    def _copy(
        self, workspace: Path, source: str, metadata_path: Path
    ) -> MaterializedRepo:
        src = Path(source).expanduser()
        if not _is_git_repo(src):
            raise RepoMaterializerError(f"copy source is not a usable git worktree: {src}")
        if _is_git_repo(workspace):
            self._verify_source(
                workspace, metadata_path, WorkspaceSourceKind.COPY.value, source
            )
            return MaterializedRepo(workspace, _git_commit(workspace), "copy")
        if _has_content(workspace):
            raise RepoMaterializerError(
                f"cannot copy into non-empty workspace: {workspace}"
            )
        if workspace.exists():
            workspace.rmdir()  # shutil.copytree requires a non-existent destination
        workspace.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(src), str(workspace), symlinks=True)
        commit = _git_commit(workspace)
        _write_metadata(metadata_path, WorkspaceSourceKind.COPY.value, source, commit)
        return MaterializedRepo(workspace, commit, "copy")

    def _bind(self, source: str) -> MaterializedRepo:
        repo = Path(source).expanduser().resolve()
        if not _is_git_repo(repo):
            raise RepoMaterializerError(
                f"LOCAL source is not a usable git repository: {repo}"
            )
        return MaterializedRepo(repo, _git_commit(repo), "local")

    def _generate(self, workspace: Path, metadata_path: Path) -> MaterializedRepo:
        if _is_git_repo(workspace):
            self._verify_source(
                workspace, metadata_path, WorkspaceSourceKind.GENERATED.value, ""
            )
            return MaterializedRepo(workspace, _git_commit(workspace), "generated")
        if _has_content(workspace):
            raise RepoMaterializerError(
                f"cannot generate into non-empty workspace: {workspace}"
            )
        workspace.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "init", "-q", str(workspace)],
            text=True,
            capture_output=True,
            timeout=self.clone_timeout_seconds,
        )
        if result.returncode != 0:
            raise RepoMaterializerError(
                f"git init failed: {(result.stderr or '').strip() or 'unknown error'}"
            )
        _write_metadata(
            metadata_path, WorkspaceSourceKind.GENERATED.value, "", ""
        )
        return MaterializedRepo(workspace, "", "generated")
