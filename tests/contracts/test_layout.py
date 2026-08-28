from resagent2_contracts import ResourceLayout, RunLayout


def test_run_layout_resolves_standard_directories(tmp_path) -> None:
    layout = RunLayout(tmp_path / "data")

    assert layout.run_dir("run_x") == tmp_path / "data" / "runs" / "run_x"
    assert layout.workspace_repo_dir("run_x", "ws_a") == (
        tmp_path / "data" / "runs" / "run_x" / "workspaces" / "ws_a" / "repo"
    )
    assert layout.workspace_meta_path("run_x", "ws_a") == (
        tmp_path / "data" / "runs" / "run_x" / "workspaces" / "ws_a" / "workspace.json"
    )
    assert layout.attempt_dir("run_x", "task_a", 2) == (
        tmp_path / "data" / "runs" / "run_x" / "attempts" / "task_a" / "attempt_2"
    )
    assert layout.artifacts_dir("run_x") == (
        tmp_path / "data" / "runs" / "run_x" / "artifacts"
    )


def test_run_layout_reads_data_root_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESAGENT2_DATA_ROOT", str(tmp_path / "custom"))
    assert RunLayout.from_env().data_root == tmp_path / "custom"


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

    # Independently-configured cache roots do not enter the resource root.
    assert layout.dataset_root == tmp_path / "elsewhere" / "datasets"
    assert layout.env_root == tmp_path / "envs"


def test_resource_layout_env_vars_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESAGENT2_DATASET_ROOT", str(tmp_path / "ds"))
    monkeypatch.setenv("RESAGENT2_ENV_ROOT", str(tmp_path / "env"))

    layout = ResourceLayout.from_env(data_root=tmp_path / "data")

    assert layout.resource_root == tmp_path / "data" / "resources"
    assert layout.dataset_root == tmp_path / "ds"
    assert layout.env_root == tmp_path / "env"


def test_resource_layout_defaults_to_data_root_resources(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("RESAGENT2_RESOURCE_ROOT", raising=False)
    monkeypatch.delenv("RESAGENT2_DATASET_ROOT", raising=False)
    monkeypatch.delenv("RESAGENT2_ENV_ROOT", raising=False)

    layout = ResourceLayout.from_env(data_root=tmp_path / "data")

    # No independent cache roots: everything derives from data_root/resources.
    assert layout.resource_root == tmp_path / "data" / "resources"
    assert layout.dataset_root == tmp_path / "data" / "resources" / "datasets"
    assert layout.env_root == tmp_path / "data" / "resources" / "envs"
