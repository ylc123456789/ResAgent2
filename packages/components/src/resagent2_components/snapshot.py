"""Attempt-start workspace snapshot: a Git tree hash with a bounded file fallback.

One value type (``WorkspaceSnapshot``), one factory (``snapshot_workspace``)
and one small observer (``WorkspaceObserver``) are shared by every consumer that
needs to attribute files or patches to a single Attempt: the Coding finalizers,
the failed-patch diagnostic, ``GitDiffTool`` and the Experiment evidence
ownership check (ADR-0011 §4).

A workspace that is a Git repository reuses the cheap content-addressed
``GitBaseline`` tree hash; anything else falls back to a bounded per-file SHA-256
map. Both forms are content-addressed, so a file restored to its original bytes
counts as unchanged under either form.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .git import GitBaseline, GitWorkspace, GitWorkspaceError
from .workspace import WorkspaceBoundary

# Bound the file-hash fallback so a non-Git workspace cannot make the snapshot
# or the increment computation O(all files) with no ceiling. Files beyond the
# sorted bound are simply absent from the snapshot and therefore never claimed
# as "changed", which is a conservative (rejecting) direction.
_MAX_FILES = 4096


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    """The Attempt-start content of a workspace, in a memory-safe form.

    Exactly one of ``tree_hash`` (Git workspace) or ``file_hashes`` (non-Git
    fallback) is set.
    """

    tree_hash: str | None = None
    file_hashes: dict[str, str] | None = None

    @property
    def git_baseline(self) -> GitBaseline | None:
        """Return the GitBaseline this snapshot carries, if it is a Git snapshot."""
        if self.tree_hash is None:
            return None
        return GitBaseline(tree_hash=self.tree_hash)

    def to_memory(self) -> dict:
        """Serialize to the single ``workspace_snapshot`` Session-memory value."""
        if self.tree_hash is not None:
            return {"kind": "git", "tree_hash": self.tree_hash}
        return {"kind": "files", "file_hashes": dict(self.file_hashes or {})}

    @classmethod
    def from_memory(cls, value: object) -> "WorkspaceSnapshot":
        """Rehydrate a snapshot from a persisted Session-memory value."""
        if not isinstance(value, dict):
            raise ValueError("workspace snapshot is not a dict")
        kind = value.get("kind")
        if kind == "git":
            tree_hash = value.get("tree_hash")
            if not isinstance(tree_hash, str) or not tree_hash:
                raise ValueError("git workspace snapshot is missing tree_hash")
            return cls(tree_hash=tree_hash)
        if kind == "files":
            file_hashes = value.get("file_hashes")
            if not isinstance(file_hashes, dict):
                raise ValueError("file workspace snapshot is missing file_hashes")
            return cls(
                file_hashes={str(k): str(v) for k, v in file_hashes.items()}
            )
        raise ValueError("unknown workspace snapshot kind")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_readable_files(
    boundary: WorkspaceBoundary, *, max_files: int = _MAX_FILES
) -> dict[str, str]:
    """Hash up to ``max_files`` readable workspace files as ``{path: sha256}``."""
    hashes: dict[str, str] = {}
    for relative in boundary.iter_files():
        if len(hashes) >= max_files:
            break
        try:
            resolved = boundary.resolve_read_file(relative)
        except (OSError, PermissionError):
            continue
        hashes[relative] = _sha256_file(resolved)
    return hashes


def snapshot_workspace(
    boundary: WorkspaceBoundary, *, max_files: int = _MAX_FILES
) -> WorkspaceSnapshot:
    """Capture the Attempt-start content of a workspace as one snapshot."""
    try:
        git = GitWorkspace(boundary)
    except (GitWorkspaceError, OSError):
        git = None
    if git is not None:
        return WorkspaceSnapshot(tree_hash=git.snapshot().tree_hash)
    return WorkspaceSnapshot(
        file_hashes=_hash_readable_files(boundary, max_files=max_files)
    )


class WorkspaceObserver:
    """Compute the Attempt increment of a workspace from one snapshot.

    Dispatches to the Git implementation when the workspace is a repository and
    to the bounded file-hash fallback otherwise, so the Git and non-Git cases
    share one snapshot value and one ``changed_paths`` answer.
    """

    def __init__(self, boundary: WorkspaceBoundary) -> None:
        self.boundary = boundary
        self._git: GitWorkspace | None = None
        self._probed = False

    def _git_workspace(self) -> GitWorkspace | None:
        if not self._probed:
            self._probed = True
            try:
                self._git = GitWorkspace(self.boundary)
            except (GitWorkspaceError, OSError):
                self._git = None
        return self._git

    @property
    def is_git(self) -> bool:
        """Whether the workspace is a Git repository (uses GitBaseline)."""
        return self._git_workspace() is not None

    def snapshot(self) -> WorkspaceSnapshot:
        """Capture the current workspace content as an Attempt-start snapshot."""
        return snapshot_workspace(self.boundary)

    def changed_paths(self, snapshot: WorkspaceSnapshot) -> list[str]:
        """Return the paths whose content differs from ``snapshot``."""
        git = self._git_workspace()
        if snapshot.tree_hash is not None and git is not None:
            return git.changed_paths_since(
                GitBaseline(tree_hash=snapshot.tree_hash)
            )
        before = snapshot.file_hashes or {}
        current = _hash_readable_files(self.boundary)
        return sorted(
            path
            for path in set(before) | set(current)
            if before.get(path) != current.get(path)
        )
