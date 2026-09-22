"""WorkspaceSnapshot / WorkspaceObserver: Git tree hash vs file-hash fallback."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from resagent2_components import (
    WorkspaceBoundary,
    WorkspaceObserver,
    WorkspaceSnapshot,
    snapshot_workspace,
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


def test_snapshot_workspace_prefers_git_tree_hash(tmp_path) -> None:
    _init_repo(tmp_path)
    snapshot = snapshot_workspace(WorkspaceBoundary(_grant(tmp_path)))

    assert snapshot.tree_hash is not None
    assert snapshot.file_hashes is None
    assert snapshot.git_baseline is not None


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


def test_snapshot_workspace_falls_back_to_file_hashes_for_non_git(tmp_path) -> None:
    (tmp_path / "data.txt").write_text("hello", encoding="utf-8")
    snapshot = snapshot_workspace(WorkspaceBoundary(_grant(tmp_path)))

    assert snapshot.tree_hash is None
    assert snapshot.file_hashes is not None
    assert "data.txt" in snapshot.file_hashes


def test_observer_changed_paths_for_git(tmp_path) -> None:
    _init_repo(tmp_path)
    observer = WorkspaceObserver(WorkspaceBoundary(_grant(tmp_path)))
    assert observer.is_git

    snapshot = observer.snapshot()
    (tmp_path / "new.py").write_text("x = 1\n", encoding="utf-8")

    assert observer.changed_paths(snapshot) == ["new.py"]


def test_observer_changed_paths_for_non_git(tmp_path) -> None:
    (tmp_path / "a.txt").write_text("one", encoding="utf-8")
    observer = WorkspaceObserver(WorkspaceBoundary(_grant(tmp_path)))
    assert not observer.is_git

    snapshot = observer.snapshot()
    (tmp_path / "a.txt").write_text("two", encoding="utf-8")
    (tmp_path / "b.txt").write_text("new", encoding="utf-8")

    assert observer.changed_paths(snapshot) == ["a.txt", "b.txt"]


@pytest.mark.parametrize(
    "snapshot",
    [
        WorkspaceSnapshot(tree_hash="abc123"),
        WorkspaceSnapshot(file_hashes={"a.txt": "deadbeef"}),
    ],
)
def test_workspace_snapshot_round_trips_through_memory(snapshot) -> None:
    assert WorkspaceSnapshot.from_memory(snapshot.to_memory()) == snapshot


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "not-a-dict",
        {},
        {"kind": "git"},
        {"kind": "files"},
        {"kind": "nope"},
    ],
)
def test_workspace_snapshot_from_memory_rejects_bad_values(bad) -> None:
    with pytest.raises(ValueError):
        WorkspaceSnapshot.from_memory(bad)
