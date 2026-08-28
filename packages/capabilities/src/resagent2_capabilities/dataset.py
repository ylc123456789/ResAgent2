"""Shared dataset cache directory and framework environment overrides."""

from __future__ import annotations

from pathlib import Path


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
