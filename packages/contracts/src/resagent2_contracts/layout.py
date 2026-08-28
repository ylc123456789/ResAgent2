"""Standard directory conventions for Run data and shared resources.

These are pure path helpers: they carry no scheduling logic. ``RunLayout`` maps
``data_root`` + ``run_id`` to per-run directories (state, workspaces, attempts,
scientific sessions, artifacts); ``ResourceLayout`` maps the shared resource
root to dataset/env/model caches. The precedence for each root is:

    explicit constructor argument
        > the matching environment variable
        > derived from resource_root
        > derived from data_root/resources
"""

from __future__ import annotations

import os
from pathlib import Path


def _env_or(default: Path, name: str) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


class RunLayout:
    """Resolve the per-run data directories under one ``data_root``."""

    def __init__(self, data_root: str | Path) -> None:
        self.data_root = Path(data_root).expanduser()

    @classmethod
    def from_env(cls) -> "RunLayout":
        return cls(os.environ.get("RESAGENT2_DATA_ROOT", ".resagent2/data"))

    def run_dir(self, run_id: str) -> Path:
        return self.data_root / "runs" / run_id

    def state_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "state"

    def workspace_dir(self, run_id: str, workspace_id: str) -> Path:
        return self.run_dir(run_id) / "workspaces" / workspace_id

    def workspace_meta_path(self, run_id: str, workspace_id: str) -> Path:
        """Path of the materialization metadata file for one managed workspace."""
        return self.workspace_dir(run_id, workspace_id) / "workspace.json"

    def workspace_repo_dir(self, run_id: str, workspace_id: str) -> Path:
        return self.workspace_dir(run_id, workspace_id) / "repo"

    def attempt_dir(self, run_id: str, task_id: str, attempt_number: int) -> Path:
        return (
            self.run_dir(run_id)
            / "attempts"
            / task_id
            / f"attempt_{attempt_number}"
        )

    def scientific_sessions_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "scientific" / "sessions"

    def artifacts_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "artifacts"


class ResourceLayout:
    """Resolve shared dataset/env/model cache roots."""

    def __init__(
        self,
        *,
        resource_root: str | Path,
        dataset_root: str | Path | None = None,
        env_root: str | Path | None = None,
    ) -> None:
        self.resource_root = Path(resource_root).expanduser()
        self.dataset_root = (
            Path(dataset_root).expanduser()
            if dataset_root
            else self.resource_root / "datasets"
        )
        self.env_root = (
            Path(env_root).expanduser() if env_root else self.resource_root / "envs"
        )

    @classmethod
    def from_env(cls, *, data_root: str | Path | None = None) -> "ResourceLayout":
        resource_root = _env_or(
            Path(data_root).expanduser() / "resources"
            if data_root
            else Path(".resagent2/data/resources"),
            "RESAGENT2_RESOURCE_ROOT",
        )
        return cls(
            resource_root=resource_root,
            dataset_root=_env_or(resource_root / "datasets", "RESAGENT2_DATASET_ROOT"),
            env_root=_env_or(resource_root / "envs", "RESAGENT2_ENV_ROOT"),
        )
