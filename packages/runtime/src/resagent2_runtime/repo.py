"""Clone, copy, or bind a repository and report its content identity."""

from __future__ import annotations

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


class RepoMaterializer:
    """Resolve exactly one of repo_url, copy_from, or external_repo_path.

    ``workspace`` is the directory that becomes (or already is) the repository
    root, so a materialized repo can be observed by a single WorkspaceBoundary.
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

    def _clone(self, workspace: Path, repo_url: str) -> MaterializedRepo:
        if _is_git_repo(workspace) and _origin_matches(workspace, repo_url):
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
        return MaterializedRepo(workspace, _git_commit(workspace), "repo_url")

    def _copy(self, workspace: Path, source: str) -> MaterializedRepo:
        src = Path(source).expanduser()
        if not _is_git_repo(src):
            raise RepoMaterializerError(f"copy_from is not a usable git worktree: {src}")
        if _is_git_repo(workspace):
            # Resume/retry: the copy already exists, reuse it.
            return MaterializedRepo(workspace, _git_commit(workspace), "copy_from")
        if _has_content(workspace):
            raise RepoMaterializerError(
                f"cannot copy into non-empty workspace: {workspace}"
            )
        if workspace.exists():
            workspace.rmdir()  # shutil.copytree requires a non-existent destination
        workspace.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(src), str(workspace), symlinks=True)
        return MaterializedRepo(workspace, _git_commit(workspace), "copy_from")

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


def _origin_matches(repo_path: Path, repo_url: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
            text=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and _normalize_url(result.stdout) == _normalize_url(
        repo_url
    )


def _normalize_url(url: str) -> str:
    normalized = url.strip().rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized
