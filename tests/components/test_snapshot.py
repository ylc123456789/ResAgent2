"""Coding Attempt Git baselines: scoped contents and persistence."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from resagent2_components import (
    WorkspaceBoundary,
    GitBaseline,
)
from resagent2_contracts import WorkspaceAccess, WorkspaceGrant, WorkspaceSourceKind


def _grant(root: Path) -> WorkspaceGrant:
    return WorkspaceGrant(
        root=str(root),
        access=WorkspaceAccess(read_paths=["."], write_paths=["."]),
        source=WorkspaceSourceKind.LOCAL,
    )


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=root, check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    (root / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)


def test_git_snapshot_does_not_copy_denied_tracked_files(tmp_path):
    from resagent2_components.git import GitWorkspace
    _init_repo(tmp_path)
    (tmp_path / "visible.txt").write_text("visible")
    grant = _grant(tmp_path)
    grant.access.denied_paths = ["tracked.txt"]
    repository = GitWorkspace(WorkspaceBoundary(grant))
    first = repository.snapshot()
    (tmp_path / "tracked.txt").write_text("SECRET_CHANGED")
    second = repository.snapshot()
    assert first.tree_hash == second.tree_hash
    paths = subprocess.run(["git", "ls-tree", "--name-only", second.tree_hash],
                           cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    assert paths.splitlines() == ["visible.txt"]
    assert repository.diff_since(first) == ""


def test_git_baseline_round_trips_through_memory():
    baseline = GitBaseline(tree_hash="abc123")
    assert GitBaseline.from_memory(baseline.to_memory()) == baseline


@pytest.mark.parametrize("bad", [
    None, "not-a-dict", {}, {"kind": "git"}, {"kind": "git", "tree_hash": ""},
    {"kind": "files", "file_hashes": {}}, {"kind": "nope"},
])
def test_git_baseline_rejects_missing_or_non_git_snapshot(bad):
    with pytest.raises(ValueError):
        GitBaseline.from_memory(bad)
