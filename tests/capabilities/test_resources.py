import json
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
    EnvironmentManagerError,
    HardwareAudit,
    RepoMaterializer,
    RepoMaterializerError,
    ResourceLayout,
    dataset_env_overrides,
    find_conda,
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
        "    prefix = args[args.index('-p') + 1]\n"
        "    os.makedirs(os.path.join(prefix, 'bin'), exist_ok=True)\n"
        "    open(os.path.join(prefix, 'bin', 'python'), 'a').close()\n"
        "print('created', flush=True)\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _manager(tmp_path: Path) -> EnvironmentManager:
    return EnvironmentManager(
        env_root=tmp_path / "resources" / "envs", conda_exe=str(_fake_conda(tmp_path))
    )


def test_env_id_is_hash_derived_and_scope_bound() -> None:
    manager = EnvironmentManager(env_root=Path("/tmp/envs"), conda_exe="conda")

    same = manager.env_id(run_id="run_a", workspace_id="ws_main")
    other_ws = manager.env_id(run_id="run_a", workspace_id="ws_other")
    other_run = manager.env_id(run_id="run_b", workspace_id="ws_main")

    assert same == manager.env_id(run_id="run_a", workspace_id="ws_main")
    assert same != other_ws
    assert same != other_run
    assert same.startswith("resenv_")
    assert len(same.split("_")[-1]) == 12


def test_prefix_is_under_env_root(tmp_path) -> None:
    manager = _manager(tmp_path)

    prefix = manager.prefix(run_id="run_a", workspace_id="ws_main")

    assert prefix == manager.env_root / manager.env_id(
        run_id="run_a", workspace_id="ws_main"
    )


def test_inspect_returns_none_for_missing_env(tmp_path) -> None:
    manager = _manager(tmp_path)

    assert manager.inspect(run_id="run_a", workspace_id="ws_main") is None


def test_prepare_creates_and_inspect_reuses(tmp_path) -> None:
    manager = _manager(tmp_path)

    prepared = manager.prepare(run_id="run_a", workspace_id="ws_main", python_version="3.12")

    assert prepared.prefix.is_dir()
    assert (prepared.prefix / ".resagent2_base_ready").is_file()
    assert prepared.python_version == "3.12"

    inspected = manager.inspect(run_id="run_a", workspace_id="ws_main")
    assert inspected is not None
    assert inspected.env_id == prepared.env_id
    assert inspected.python_version == "3.12"


def test_prepare_recreates_partial_env(tmp_path) -> None:
    manager = _manager(tmp_path)
    partial = manager.prefix(run_id="run_a", workspace_id="ws_main")
    partial.mkdir(parents=True)
    (partial / "junk.txt").write_text("partial", encoding="utf-8")

    prepared = manager.prepare(run_id="run_a", workspace_id="ws_main", python_version="3.12")

    assert not (partial / "junk.txt").exists()
    assert (partial / ".resagent2_base_ready").is_file()
    assert prepared.python_version == "3.12"


def test_prepare_does_not_install_dependencies(tmp_path) -> None:
    manager = _manager(tmp_path)

    prepared = manager.prepare(run_id="run_a", workspace_id="ws_main", python_version="3.12")

    marker = json.loads(
        (prepared.prefix / ".resagent2_base_ready").read_text(encoding="utf-8")
    )
    assert marker["python_version"] == "3.12"
    assert marker["env_id"] == prepared.env_id
    # The manager never installs project dependencies: no pip was invoked.
    assert not (prepared.prefix / "bin" / "pip").exists()


def test_delete_if_managed_refuses_outside_env_root(tmp_path) -> None:
    manager = EnvironmentManager(env_root=tmp_path / "envs", conda_exe="conda")
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(EnvironmentManagerError, match="outside"):
        manager._delete_if_managed(outside)


def test_audit_returns_structured_result(tmp_path) -> None:
    manager = _manager(tmp_path)
    prepared = manager.prepare(run_id="run_a", workspace_id="ws_main", python_version="3.12")

    audit = manager.audit(prepared)

    assert set(audit) >= {"success", "pip_available", "prefix_match", "python_version"}


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


def test_resolve_dataset_refs_rejects_duplicate_id(tmp_path) -> None:
    root = tmp_path / "datasets"
    (root / "cifar10").mkdir(parents=True)

    with pytest.raises(DatasetResolutionError, match="duplicate"):
        resolve_dataset_refs(
            root,
            [
                DatasetRef(dataset_id="cifar10", relative_path="cifar10"),
                DatasetRef(dataset_id="cifar10", relative_path="cifar10"),
            ],
        )


def test_dataset_env_overrides_are_generic(tmp_path) -> None:
    root = tmp_path / "datasets"
    (root / "cifar10").mkdir(parents=True)
    (root / "mnist").mkdir(parents=True)
    resolved = resolve_dataset_refs(
        root,
        [
            DatasetRef(dataset_id="cifar10", relative_path="cifar10"),
            DatasetRef(dataset_id="mnist", relative_path="mnist"),
        ],
    )

    overrides = dataset_env_overrides(root, resolved)

    assert "TORCHVISION_DATASETS" not in overrides
    assert overrides["RESAGENT2_DATASET_ROOT"] == str(root.resolve())
    assert json.loads(overrides["RESAGENT2_DATASETS_JSON"]) == {
        "cifar10": str((root / "cifar10").resolve()),
        "mnist": str((root / "mnist").resolve()),
    }
