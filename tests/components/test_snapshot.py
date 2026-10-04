"""Coding Attempt Git baselines: scoped contents and persistence."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from resagent2_components import (
    WorkspaceBoundary,
    GitBaseline,
)
from resagent2_components.git import GitWorkspace, GitWorkspaceError
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


@pytest.fixture
def submodule_repo(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _init_repo(source)
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    subprocess.run(
        ["git", "-C", str(root), "-c", "protocol.file.allow=always",
         "submodule", "add", str(source), "vendor/model"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "submodule"],
        check=True, capture_output=True,
    )
    return root


@pytest.mark.parametrize("read_paths", [["."], ["vendor/model/tracked.txt"]])
def test_snapshot_rejects_submodule_in_readable_scope(submodule_repo, read_paths):
    grant = _grant(submodule_repo)
    grant.access.read_paths = read_paths
    grant.access.write_paths = read_paths
    repository = GitWorkspace(WorkspaceBoundary(grant))

    with pytest.raises(GitWorkspaceError, match="do not support submodule contents.*vendor/model"):
        repository.snapshot()


def test_submodule_mutation_cannot_be_reported_as_unchanged(submodule_repo):
    repository = GitWorkspace(WorkspaceBoundary(_grant(submodule_repo)))
    # A persisted baseline does not let later observations bypass the same check.
    tree = subprocess.run(
        ["git", "-C", str(submodule_repo), "rev-parse", "HEAD^{tree}"],
        check=True, text=True, capture_output=True,
    ).stdout.strip()
    baseline = GitBaseline(tree_hash=tree)
    (submodule_repo / "vendor/model/tracked.txt").write_text("changed during verification\n")

    with pytest.raises(GitWorkspaceError, match="do not support submodule contents"):
        repository.changed_paths_since(baseline)
    with pytest.raises(GitWorkspaceError, match="do not support submodule contents"):
        repository.diff_since(baseline)


@pytest.mark.parametrize("scope", ["denied", "unrelated"])
def test_submodule_outside_readable_scope_does_not_block_snapshot(submodule_repo, scope):
    grant = _grant(submodule_repo)
    if scope == "denied":
        grant.access.denied_paths = ["vendor/model"]
    else:
        grant.access.read_paths = ["tracked.txt"]
        grant.access.write_paths = ["tracked.txt"]
    repository = GitWorkspace(WorkspaceBoundary(grant))
    baseline = repository.snapshot()
    (submodule_repo / "vendor/model/tracked.txt").write_text("outside authorized snapshot\n")

    assert repository.diff_since(baseline) == ""
    (submodule_repo / "tracked.txt").write_text("visible change\n")
    assert repository.changed_paths_since(baseline) == ["tracked.txt"]
    assert "+visible change" in repository.diff_since(baseline)


def test_uninitialized_submodule_remains_an_unsupported_readable_snapshot(submodule_repo):
    subprocess.run(
        ["git", "-C", str(submodule_repo), "submodule", "deinit", "--force", "--all"],
        check=True, capture_output=True,
    )
    repository = GitWorkspace(WorkspaceBoundary(_grant(submodule_repo)))

    with pytest.raises(GitWorkspaceError, match="do not support submodule contents"):
        repository.snapshot()
