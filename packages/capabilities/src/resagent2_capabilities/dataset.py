"""Shared dataset cache directory and framework environment overrides."""

from __future__ import annotations

from pathlib import Path

from resagent2_contracts import DatasetRef


class DatasetResolutionError(ValueError):
    """Raised when a task-level dataset reference cannot be safely resolved."""


def resolve_dataset_refs(
    dataset_root: str | Path, refs: list[DatasetRef]
) -> list[dict]:
    """Resolve task-level dataset references to read-only paths under the root.

    Each reference's ``relative_path`` is joined under ``dataset_root``, then
    checked for directory escape (``..`` / absolute) and existence. The result
    is a list of ``{dataset_id, path, access}`` entries; the dataset root is the
    shared "all datasets" directory, never one specific dataset.
    """
    root = Path(dataset_root).expanduser().resolve()
    resolved: list[dict] = []
    for ref in refs:
        candidate = (root / ref.relative_path).resolve()
        if not candidate.is_relative_to(root):
            raise DatasetResolutionError(
                f"dataset relative_path escapes the root: {ref.relative_path!r}"
            )
        if not candidate.is_dir():
            raise DatasetResolutionError(f"dataset path does not exist: {candidate}")
        resolved.append(
            {
                "dataset_id": ref.dataset_id,
                "path": str(candidate),
                "access": "read_only",
            }
        )
    return resolved


_CACHE_ENV_VARS = (
    "TORCH_HOME",
    "TORCHVISION_DATASETS",
    "HF_HOME",
    "HUGGINGFACE_HUB_CACHE",
    "TORCH_HUB",
)


# Best-effort mirror acceleration profiles. These are operational overrides
# (never part of environment identity) and are intentionally small.
_MIRROR_PROFILES: dict[str, dict[str, str]] = {
    "none": {},
    "cn": {
        "PIP_INDEX_URL": "https://pypi.tuna.tsinghua.edu.cn/simple",
    },
    "autodl": {
        "PIP_INDEX_URL": "https://mirrors.cloud.tencent.com/pypi/simple",
    },
}


class DatasetCache:
    """Resolve a shared cache root and produce framework env overrides."""

    def __init__(self, *, root: str | Path) -> None:
        self.root = Path(root).expanduser()

    def env_overrides(self) -> dict[str, str]:
        """Point every supported framework cache at the shared root."""
        return {name: str(self.root) for name in _CACHE_ENV_VARS}


def mirror_env_overrides(profile: str) -> dict[str, str]:
    """Return mirror env overrides for a named profile (``none`` is a no-op)."""
    return dict(_MIRROR_PROFILES.get(profile, {}))
