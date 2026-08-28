"""Shared resource directory layout (dataset/env/model caches)."""

from __future__ import annotations

import os
from pathlib import Path


def _env_or(default: Path, name: str) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


class ResourceLayout:
    """Resolve shared dataset/env/model cache roots.

    Precedence: explicit constructor argument > the matching environment
    variable > derived from ``resource_root`` > derived from
    ``data_root/resources``. ``env_root`` is the directory that directly holds
    conda environment prefixes (no extra ``envs`` segment is appended).
    """

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
        if data_root is None:
            data_root = os.environ.get("RESAGENT2_DATA_ROOT")
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
