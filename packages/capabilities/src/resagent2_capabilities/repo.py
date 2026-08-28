"""Clone, copy, or bind a repository and report its content identity."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class RepoMaterializerError(ValueError):
    """Raised when a repository source is missing, conflicting, or unusable."""


@dataclass(frozen=True, slots=True)
class MaterializedRepo:
    """A resolved repository worktree plus its content identity."""

    repo_path: Path
    commit: str
    source: str


_METADATA_FILENAME = ".resagent2/materialized_source.json"


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


def _metadata_path(workspace: Path) -> Path:
    return workspace / _METADATA_FILENAME


def _read_metadata(workspace: Path) -> dict | None:
    path = _metadata_path(workspace)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_metadata(workspace: Path, source_type: str, source: str, commit: str) -> None:
    path = _metadata_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"source_type": source_type, "source": source, "commit": commit}),
        encoding="utf-8",
    )


class RepoMaterializer:
    """Resolve exactly one of repo_url, copy_from, or external_repo_path.

    ``workspace`` is the directory that becomes (or already is) the repository
    root. For clone/copy sources a small runtime metadata file records which
    source materialized the workspace, so a retry verifies it is reusing the
    same repository rather than silently adopting a leftover from another one.
    """

    def __init__(self, *, clone_timeout_seconds: int = 300) -> None:
        self.clone_timeout_seconds = clone_timeout_seconds

    def materialize(
        self,
        *,
        workspace: Path,
        repo_url: str = "",
        copy_from: str = "",
        external_repo_path: str = "",
    ) -> MaterializedRepo:
        """Return the workspace repository for the single declared source."""
        sources = [
            name
            for name, value in (
                ("repo_url", repo_url),
                ("copy_from", copy_from),
                ("external_repo_path", external_repo_path),
            )
            if value
        ]
        if len(sources) > 1:
            raise RepoMaterializerError(
                f"exactly one repository source is allowed, got: {', '.join(sources)}"
            )
        workspace = Path(workspace).expanduser().resolve()
        if repo_url:
            return self._clone(workspace, repo_url)
        if copy_from:
            return self._copy(workspace, copy_from)
        if external_repo_path:
            return self._bind(external_repo_path)
        return self._resume(workspace)

    def _verify_source(
        self, workspace: Path, expected_type: str, expected_source: str
    ) -> None:
        metadata = _read_metadata(workspace)
        if metadata is None:
            raise RepoMaterializerError(
                f"workspace at {workspace} has no materialization metadata; "
                f"cannot verify it came from {expected_source!r}"
            )
        actual_type = metadata.get("source_type")
        if actual_type != expected_type:
            raise RepoMaterializerError(
                f"workspace was materialized as {actual_type!r}, not {expected_type!r}"
            )
        if _normalize_source(metadata.get("source", "")) != _normalize_source(
            expected_source
        ):
            raise RepoMaterializerError(
                f"workspace source {metadata.get('source')!r} does not match "
                f"requested {expected_source!r}"
            )

    def _clone(self, workspace: Path, repo_url: str) -> MaterializedRepo:
        if _is_git_repo(workspace):
            self._verify_source(workspace, "repo_url", repo_url)
            return MaterializedRepo(workspace, _git_commit(workspace), "repo_url")
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
        _write_metadata(workspace, "repo_url", repo_url, commit)
        return MaterializedRepo(workspace, commit, "repo_url")

    def _copy(self, workspace: Path, source: str) -> MaterializedRepo:
        src = Path(source).expanduser()
        if not _is_git_repo(src):
            raise RepoMaterializerError(f"copy_from is not a usable git worktree: {src}")
        if _is_git_repo(workspace):
            self._verify_source(workspace, "copy_from", source)
            return MaterializedRepo(workspace, _git_commit(workspace), "copy_from")
        if _has_content(workspace):
            raise RepoMaterializerError(
                f"cannot copy into non-empty workspace: {workspace}"
            )
        if workspace.exists():
            workspace.rmdir()  # shutil.copytree requires a non-existent destination
        workspace.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(src), str(workspace), symlinks=True)
        commit = _git_commit(workspace)
        _write_metadata(workspace, "copy_from", source, commit)
        return MaterializedRepo(workspace, commit, "copy_from")

    def _bind(self, source: str) -> MaterializedRepo:
        repo = Path(source).expanduser().resolve()
        if not _is_git_repo(repo):
            raise RepoMaterializerError(
                f"external_repo_path is not a usable git repository: {repo}"
            )
        return MaterializedRepo(repo, _git_commit(repo), "external_repo_path")

    def _resume(self, workspace: Path) -> MaterializedRepo:
        if not _is_git_repo(workspace):
            raise RepoMaterializerError(
                "no repository source given and the workspace is not a usable repo at "
                f"{workspace}; provide repo_url, copy_from, or external_repo_path"
            )
        return MaterializedRepo(workspace, _git_commit(workspace), "")
