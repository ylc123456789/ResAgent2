import os
import subprocess
import sys
from pathlib import Path

import pytest

from resagent2_runtime import (
    DatasetCache,
    EnvironmentManager,
    HardwareAudit,
    RepoMaterializer,
    RepoMaterializerError,
    env_id,
    env_spec,
    find_conda,
    project_slug,
    resource_root,
)
from resagent2_runtime.process import _descendant_pids


def _init_repo(root: Path, *, commit_file: str = "tracked.txt") -> str:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    (root / commit_file).write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


# ── RepoMaterializer ────────────────────────────────────────────────


def test_materialize_clones_repo_url(tmp_path) -> None:
    source = tmp_path / "source"
    commit = _init_repo(source)
    materialized = RepoMaterializer().materialize(
        workspace=tmp_path / "work", repo_url=str(source)
    )

    assert materialized.repo_path.is_dir()
    assert materialized.commit == commit
    assert materialized.source == "repo_url"


def test_materialize_copy_from_preserves_worktree(tmp_path) -> None:
    source = tmp_path / "source"
    commit = _init_repo(source)
    (source / "uncommitted.py").write_text("dirty = True\n", encoding="utf-8")
    materialized = RepoMaterializer().materialize(
        workspace=tmp_path / "work", copy_from=str(source)
    )

    assert materialized.commit == commit
    assert materialized.source == "copy_from"
    assert (materialized.repo_path / "uncommitted.py").read_text(encoding="utf-8") == (
        "dirty = True\n"
    )


def test_materialize_copy_from_is_idempotent_on_resume(tmp_path) -> None:
    source = tmp_path / "source"
    commit = _init_repo(source)
    materializer = RepoMaterializer()

    first = materializer.materialize(workspace=tmp_path / "work", copy_from=str(source))
    second = materializer.materialize(workspace=tmp_path / "work", copy_from=str(source))

    assert first.repo_path == second.repo_path
    assert first.commit == second.commit == commit


def test_materialize_copy_into_preexisting_empty_dir(tmp_path) -> None:
    source = tmp_path / "source"
    _init_repo(source)
    workspace = tmp_path / "work"
    workspace.mkdir()

    materialized = RepoMaterializer().materialize(workspace=workspace, copy_from=str(source))

    assert materialized.repo_path.is_dir()
    assert materialized.source == "copy_from"


def test_materialize_binds_external_repo_in_place(tmp_path) -> None:
    source = tmp_path / "source"
    commit = _init_repo(source)
    materialized = RepoMaterializer().materialize(
        workspace=tmp_path / "work", external_repo_path=str(source)
    )

    assert materialized.repo_path.resolve() == source.resolve()
    assert materialized.commit == commit
    assert materialized.source == "external_repo_path"


def test_materialize_rejects_conflicting_sources(tmp_path) -> None:
    with pytest.raises(RepoMaterializerError, match="exactly one"):
        RepoMaterializer().materialize(
            workspace=tmp_path,
            repo_url="https://example.com/repo.git",
            copy_from=str(tmp_path),
        )


def test_materialize_resume_reuses_existing_repo(tmp_path) -> None:
    workspace = tmp_path / "work"
    commit = _init_repo(workspace)
    materialized = RepoMaterializer().materialize(workspace=workspace)

    assert materialized.repo_path == workspace
    assert materialized.commit == commit


def test_materialize_resume_without_repo_fails(tmp_path) -> None:
    with pytest.raises(RepoMaterializerError, match="no repository source"):
        RepoMaterializer().materialize(workspace=tmp_path)


# ── environment identity ───────────────────────────────────────────


def test_project_slug_sanitizes_and_lowercases() -> None:
    assert project_slug("My Repo.git") == "my-repo-git"
    assert project_slug("  ") == "project"
    assert project_slug("a--b") == "a-b"


def test_env_id_is_content_addressed_and_ignores_basename() -> None:
    spec = {"python": "3.12", "files": []}
    same_url_a = env_id("repo", "https://host/alice/repo.git\0abc", spec)
    same_url_b = env_id("repo", "https://host/alice/repo.git\0abc", spec)
    other_commit = env_id("repo", "https://host/alice/repo.git\0def", spec)
    other_url = env_id("repo", "https://host/bob/repo.git\0abc", spec)

    assert same_url_a == same_url_b
    assert other_commit != same_url_a
    assert other_url != same_url_a  # same basename, different URL -> different id
    assert same_url_a.startswith("resenv_repo_")
    assert len(same_url_a.split("_")[-1]) == 12


def test_env_spec_hashes_dependency_files(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("numpy==1.26\n", encoding="utf-8")
    (repo / "train.py").write_text("x = 1\n", encoding="utf-8")

    spec = env_spec(repo, "3.12")

    assert spec["python"] == "3.12"
    assert set(spec["files"]) == {"requirements.txt"}
    assert all(len(digest) == 64 for digest in spec["files"].values())


def test_resource_root_respects_env_var(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESAGENT2_RESOURCE_ROOT", str(tmp_path / "shared"))
    assert resource_root() == (tmp_path / "shared")


def test_find_conda_prefers_configured_exe(tmp_path, monkeypatch) -> None:
    fake = tmp_path / "conda"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("RESAGENT2_CONDA_EXE", str(fake))

    assert find_conda() == str(fake)


# ── EnvironmentManager ─────────────────────────────────────────────


def _fake_conda(tmp_path: Path) -> Path:
    fake = tmp_path / "conda"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "args = sys.argv[1:]\n"
        "if '-p' in args:\n"
        "    os.makedirs(args[args.index('-p') + 1], exist_ok=True)\n"
        "print('created', flush=True)\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def test_environment_manager_prefix_is_content_addressed(tmp_path) -> None:
    manager = EnvironmentManager(root=tmp_path / "resources")

    assert manager.prefix("resenv_x") == tmp_path / "resources" / "envs" / "resenv_x"


def test_environment_manager_reuses_existing_prefix(tmp_path) -> None:
    manager = EnvironmentManager(root=tmp_path / "resources", conda_exe=str(_fake_conda(tmp_path)))
    prefix = manager.prefix("resenv_x")
    prefix.mkdir(parents=True)

    result = manager.ensure(
        identifier="resenv_x", repo_path=tmp_path, python_version="3.12"
    )

    assert result == prefix


def test_environment_manager_creates_prefix_via_conda(tmp_path) -> None:
    manager = EnvironmentManager(root=tmp_path / "resources", conda_exe=str(_fake_conda(tmp_path)))

    result = manager.ensure(
        identifier="resenv_x", repo_path=tmp_path, python_version="3.12"
    )

    assert result.is_dir()
    assert result == tmp_path / "resources" / "envs" / "resenv_x"


# ── DatasetCache / HardwareAudit ───────────────────────────────────


def test_dataset_cache_env_overrides_point_at_root(tmp_path) -> None:
    cache = DatasetCache(root=tmp_path / "datasets")

    overrides = cache.env_overrides()

    assert overrides["TORCH_HOME"] == str(tmp_path / "datasets")
    assert overrides["HF_HOME"] == str(tmp_path / "datasets")


def test_hardware_audit_returns_structured_summary() -> None:
    info = HardwareAudit().collect()

    assert "os" in info
    assert "cpu_cores" in info
    assert "gpus" in info


def test_descendant_pids_finds_child_process() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    try:
        assert child.pid in _descendant_pids(os.getpid())
    finally:
        child.terminate()
        child.wait()
