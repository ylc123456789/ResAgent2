import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from resagent2_capabilities import (
    DatasetCache,
    DatasetResolutionError,
    EnvironmentManager,
    HardwareAudit,
    RepoMaterializer,
    RepoMaterializerError,
    ResourceLayout,
    env_id,
    env_spec,
    find_conda,
    project_slug,
    resolve_dataset_refs,
)
from resagent2_capabilities.process import _descendant_pids
from resagent2_contracts import DatasetRef, WorkspaceSourceKind, WorkspaceSpec


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


def _spec(kind: WorkspaceSourceKind, location: str | None = None) -> WorkspaceSpec:
    return WorkspaceSpec(workspace_id="ws_main", source_kind=kind, location=location)


def test_materialize_clones_git_source(tmp_path) -> None:
    source = tmp_path / "source"
    commit = _init_repo(source)
    materialized = RepoMaterializer().materialize(
        workspace=tmp_path / "work", source=_spec(WorkspaceSourceKind.GIT, str(source))
    )

    assert materialized.repo_path.is_dir()
    assert materialized.commit == commit
    assert materialized.source == "git"


def test_materialize_copy_preserves_worktree(tmp_path) -> None:
    source = tmp_path / "source"
    commit = _init_repo(source)
    (source / "uncommitted.py").write_text("dirty = True\n", encoding="utf-8")
    materialized = RepoMaterializer().materialize(
        workspace=tmp_path / "work", source=_spec(WorkspaceSourceKind.COPY, str(source))
    )

    assert materialized.commit == commit
    assert materialized.source == "copy"
    assert (materialized.repo_path / "uncommitted.py").read_text(encoding="utf-8") == (
        "dirty = True\n"
    )


def test_materialize_copy_is_idempotent_on_resume(tmp_path) -> None:
    source = tmp_path / "source"
    commit = _init_repo(source)
    materializer = RepoMaterializer()
    spec = _spec(WorkspaceSourceKind.COPY, str(source))

    first = materializer.materialize(workspace=tmp_path / "work", source=spec)
    second = materializer.materialize(workspace=tmp_path / "work", source=spec)

    assert first.repo_path == second.repo_path
    assert first.commit == second.commit == commit


def test_materialize_copy_into_preexisting_empty_dir(tmp_path) -> None:
    source = tmp_path / "source"
    _init_repo(source)
    workspace = tmp_path / "work"
    workspace.mkdir()

    materialized = RepoMaterializer().materialize(
        workspace=workspace, source=_spec(WorkspaceSourceKind.COPY, str(source))
    )

    assert materialized.repo_path.is_dir()
    assert materialized.source == "copy"


def test_copy_cannot_reuse_workspace_from_other_source(tmp_path) -> None:
    source_a = tmp_path / "source_a"
    source_b = tmp_path / "source_b"
    _init_repo(source_a)
    _init_repo(source_b)
    workspace = tmp_path / "work"
    RepoMaterializer().materialize(
        workspace=workspace, source=_spec(WorkspaceSourceKind.COPY, str(source_a))
    )

    with pytest.raises(RepoMaterializerError, match="does not match"):
        RepoMaterializer().materialize(
            workspace=workspace, source=_spec(WorkspaceSourceKind.COPY, str(source_b))
        )


def test_missing_metadata_cannot_pretend_source_match(tmp_path) -> None:
    source = tmp_path / "source"
    _init_repo(source)
    workspace = tmp_path / "work"
    _init_repo(workspace)  # a git repo, but no materialization metadata

    with pytest.raises(RepoMaterializerError, match="metadata"):
        RepoMaterializer().materialize(
            workspace=workspace, source=_spec(WorkspaceSourceKind.COPY, str(source))
        )


def test_corrupt_metadata_cannot_pretend_source_match(tmp_path) -> None:
    source = tmp_path / "source"
    _init_repo(source)
    workspace = tmp_path / "work"
    _init_repo(workspace)
    (workspace.parent / "workspace.json").write_text("not json", encoding="utf-8")

    with pytest.raises(RepoMaterializerError, match="metadata"):
        RepoMaterializer().materialize(
            workspace=workspace, source=_spec(WorkspaceSourceKind.COPY, str(source))
        )


def test_source_mismatch_is_a_structured_error(tmp_path) -> None:
    source = tmp_path / "source"
    _init_repo(source)
    workspace = tmp_path / "work"
    RepoMaterializer().materialize(
        workspace=workspace, source=_spec(WorkspaceSourceKind.COPY, str(source))
    )

    with pytest.raises(RepoMaterializerError, match="materialized as"):
        RepoMaterializer().materialize(
            workspace=workspace,
            source=_spec(WorkspaceSourceKind.GIT, "https://example.com/other.git"),
        )


def test_materialized_commit_matches_actual_repo(tmp_path) -> None:
    source = tmp_path / "source"
    commit = _init_repo(source)
    materialized = RepoMaterializer().materialize(
        workspace=tmp_path / "work", source=_spec(WorkspaceSourceKind.COPY, str(source))
    )

    actual = subprocess.run(
        ["git", "-C", str(materialized.repo_path), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()

    assert materialized.commit == commit == actual


def test_materialize_binds_local_in_place(tmp_path) -> None:
    source = tmp_path / "source"
    commit = _init_repo(source)
    materialized = RepoMaterializer().materialize(
        workspace=tmp_path / "work", source=_spec(WorkspaceSourceKind.LOCAL, str(source))
    )

    assert materialized.repo_path.resolve() == source.resolve()
    assert materialized.commit == commit
    assert materialized.source == "local"


def test_materialize_generated_creates_empty_managed_workspace(tmp_path) -> None:
    materialized = RepoMaterializer().materialize(
        workspace=tmp_path / "work", source=_spec(WorkspaceSourceKind.GENERATED)
    )

    assert materialized.repo_path.is_dir()
    assert materialized.source == "generated"
    assert (materialized.repo_path / ".git").is_dir()


def test_git_source_requires_location() -> None:
    with pytest.raises(ValidationError, match="requires a location"):
        WorkspaceSpec(workspace_id="ws_main", source_kind=WorkspaceSourceKind.GIT)


def test_local_source_requires_location() -> None:
    with pytest.raises(ValidationError, match="requires a location"):
        WorkspaceSpec(workspace_id="ws_main", source_kind=WorkspaceSourceKind.LOCAL)


def test_copy_source_requires_location() -> None:
    with pytest.raises(ValidationError, match="requires a location"):
        WorkspaceSpec(workspace_id="ws_main", source_kind=WorkspaceSourceKind.COPY)


def test_generated_source_forbids_location() -> None:
    with pytest.raises(ValidationError, match="must not have a location"):
        WorkspaceSpec(
            workspace_id="ws_main",
            source_kind=WorkspaceSourceKind.GENERATED,
            location="/tmp/x",
        )


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


def test_resource_layout_respects_resource_root_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESAGENT2_RESOURCE_ROOT", str(tmp_path / "shared"))
    layout = ResourceLayout.from_env(data_root=tmp_path / "data")
    assert layout.resource_root == tmp_path / "shared"


def test_resource_layout_defaults_derive_from_resource_root(tmp_path) -> None:
    layout = ResourceLayout(resource_root=tmp_path / "resources")

    assert layout.dataset_root == tmp_path / "resources" / "datasets"
    assert layout.env_root == tmp_path / "resources" / "envs"


def test_resource_layout_accepts_independent_roots(tmp_path) -> None:
    layout = ResourceLayout(
        resource_root=tmp_path / "resources",
        dataset_root=tmp_path / "elsewhere" / "datasets",
        env_root=tmp_path / "envs",
    )

    assert layout.dataset_root == tmp_path / "elsewhere" / "datasets"
    assert layout.env_root == tmp_path / "envs"


def test_resource_layout_env_vars_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESAGENT2_DATASET_ROOT", str(tmp_path / "ds"))
    monkeypatch.setenv("RESAGENT2_ENV_ROOT", str(tmp_path / "env"))

    layout = ResourceLayout.from_env(data_root=tmp_path / "data")

    assert layout.resource_root == tmp_path / "data" / "resources"
    assert layout.dataset_root == tmp_path / "ds"
    assert layout.env_root == tmp_path / "env"


def test_resource_layout_reads_data_root_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("RESAGENT2_RESOURCE_ROOT", raising=False)
    monkeypatch.setenv("RESAGENT2_DATA_ROOT", str(tmp_path / "data"))

    layout = ResourceLayout.from_env()

    assert layout.resource_root == tmp_path / "data" / "resources"
    assert layout.env_root == tmp_path / "data" / "resources" / "envs"


def test_resource_layout_defaults_to_data_root_resources(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("RESAGENT2_RESOURCE_ROOT", raising=False)
    monkeypatch.delenv("RESAGENT2_DATASET_ROOT", raising=False)
    monkeypatch.delenv("RESAGENT2_ENV_ROOT", raising=False)

    layout = ResourceLayout.from_env(data_root=tmp_path / "data")

    assert layout.resource_root == tmp_path / "data" / "resources"
    assert layout.dataset_root == tmp_path / "data" / "resources" / "datasets"
    assert layout.env_root == tmp_path / "data" / "resources" / "envs"


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
    manager = EnvironmentManager(env_root=tmp_path / "resources" / "envs")

    assert manager.prefix("resenv_x") == tmp_path / "resources" / "envs" / "resenv_x"


def test_environment_manager_reuses_existing_prefix(tmp_path) -> None:
    manager = EnvironmentManager(
        env_root=tmp_path / "resources" / "envs", conda_exe=str(_fake_conda(tmp_path))
    )
    prefix = manager.prefix("resenv_x")
    prefix.mkdir(parents=True)

    result = manager.ensure(
        identifier="resenv_x", repo_path=tmp_path, python_version="3.12"
    )

    assert result == prefix


def test_environment_manager_creates_prefix_via_conda(tmp_path) -> None:
    manager = EnvironmentManager(
        env_root=tmp_path / "resources" / "envs", conda_exe=str(_fake_conda(tmp_path))
    )

    result = manager.ensure(
        identifier="resenv_x", repo_path=tmp_path, python_version="3.12"
    )

    assert result.is_dir()
    assert result == tmp_path / "resources" / "envs" / "resenv_x"


def test_environment_manager_installs_requirements_txt(tmp_path) -> None:
    prefix = tmp_path / "resources" / "envs" / "resenv_x"
    bin_dir = prefix / "bin"
    bin_dir.mkdir(parents=True)
    called = tmp_path / "pip_called"
    (bin_dir / "pip").write_text(f"#!/bin/sh\ntouch {called}\n", encoding="utf-8")
    (bin_dir / "pip").chmod(0o755)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("torch>=2.0\n", encoding="utf-8")

    manager = EnvironmentManager(
        env_root=tmp_path / "resources" / "envs", conda_exe="unused"
    )
    result = manager.ensure(
        identifier="resenv_x", repo_path=repo, python_version="3.12"
    )

    assert result == prefix
    assert called.is_file()  # pip install -r was invoked
    assert (prefix / ".resagent2_env_ready").is_file()  # ready marker written


def test_environment_manager_reuses_ready_env_without_reinstall(tmp_path) -> None:
    prefix = tmp_path / "resources" / "envs" / "resenv_x"
    prefix.mkdir(parents=True)
    (prefix / ".resagent2_env_ready").write_text("ready", encoding="utf-8")

    manager = EnvironmentManager(
        env_root=tmp_path / "resources" / "envs", conda_exe="unused"
    )
    result = manager.ensure(
        identifier="resenv_x", repo_path=tmp_path, python_version="3.12"
    )

    assert result == prefix


# ── DatasetCache / HardwareAudit ───────────────────────────────────


def test_dataset_cache_env_overrides_point_at_root(tmp_path) -> None:
    cache = DatasetCache(root=tmp_path / "datasets")

    overrides = cache.env_overrides()

    assert overrides["TORCH_HOME"] == str(tmp_path / "datasets")
    assert overrides["HF_HOME"] == str(tmp_path / "datasets")


def test_resolve_dataset_refs_resolves_multiple_read_only_paths(tmp_path) -> None:
    root = tmp_path / "datasets"
    (root / "cifar10").mkdir(parents=True)
    (root / "mnist").mkdir(parents=True)
    refs = [
        DatasetRef(dataset_id="cifar10", relative_path="cifar10"),
        DatasetRef(dataset_id="mnist", relative_path="mnist"),
    ]

    resolved = resolve_dataset_refs(root, refs)

    assert [entry["dataset_id"] for entry in resolved] == ["cifar10", "mnist"]
    assert resolved[0]["path"] == str((root / "cifar10").resolve())
    assert resolved[1]["path"] == str((root / "mnist").resolve())
    assert all(entry["access"] == "read_only" for entry in resolved)


def test_resolve_dataset_refs_rejects_missing_path(tmp_path) -> None:
    root = tmp_path / "datasets"
    root.mkdir()

    with pytest.raises(DatasetResolutionError, match="does not exist"):
        resolve_dataset_refs(
            root, [DatasetRef(dataset_id="x", relative_path="missing")]
        )


def test_dataset_ref_rejects_escaping_relative_path() -> None:
    with pytest.raises(ValidationError, match="relative"):
        DatasetRef(dataset_id="x", relative_path="../escape")


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
