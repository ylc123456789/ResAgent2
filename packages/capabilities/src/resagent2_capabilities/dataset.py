"""Shared dataset catalog, context and execution bindings."""

from __future__ import annotations

import json
from pathlib import Path

from resagent2_contracts import DatasetRef


# Generic dataset hand-off surface. These are the only dataset-related env vars
# the Experiment Agent promises to its scripts: nothing framework-specific and
# no "first dataset" special-casing. Model/Hub caches (TORCH_HOME/HF_HOME/...)
# are deliberately kept out of the dataset root.
RESAGENT2_DATASET_ROOT = "RESAGENT2_DATASET_ROOT"
RESAGENT2_DATASETS_JSON = "RESAGENT2_DATASETS_JSON"
DATASET_CATALOG_FILENAME = "catalog.json"


class DatasetResolutionError(ValueError):
    """Raised when a task-level dataset reference cannot be safely resolved."""


class DatasetCatalog:
    """Read the deployment-owned ``dataset_id -> relative path`` catalog.

    The catalog lives under the shared dataset root and is the only place where
    physical dataset directories are registered.  An absent catalog means that
    no datasets are available; a malformed catalog or missing registered path
    is an explicit configuration error.
    """

    def __init__(self, dataset_root: str | Path) -> None:
        self.dataset_root = Path(dataset_root).expanduser().resolve()

    @property
    def path(self) -> Path:
        return self.dataset_root / DATASET_CATALOG_FILENAME

    def references(self) -> list[DatasetRef]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DatasetResolutionError(
                f"cannot read dataset catalog {self.path}: {error}"
            ) from error
        if not isinstance(raw, dict) or not all(
            isinstance(dataset_id, str) and isinstance(relative_path, str)
            for dataset_id, relative_path in raw.items()
        ):
            raise DatasetResolutionError(
                "dataset catalog must be a JSON object mapping ids to relative paths"
            )
        try:
            refs = [
                DatasetRef(dataset_id=dataset_id, relative_path=relative_path)
                for dataset_id, relative_path in sorted(raw.items())
            ]
        except ValueError as error:
            raise DatasetResolutionError(f"invalid dataset catalog: {error}") from error
        resolve_dataset_refs(self.dataset_root, refs)
        return refs


def dataset_context(refs: list[DatasetRef]) -> dict:
    """Render one compact policy/context payload shared by all Agents."""

    return {
        "available_dataset_ids": sorted(ref.dataset_id for ref in refs),
        "access": "read_only",
        "environment": {
            "root": RESAGENT2_DATASET_ROOT,
            "id_to_path_map": RESAGENT2_DATASETS_JSON,
        },
        "missing_dataset_action": "ask_user",
        "download_allowed": False,
        "substitution_allowed": False,
    }


def resolve_dataset_refs(
    dataset_root: str | Path, refs: list[DatasetRef]
) -> list[dict]:
    """Resolve task-level dataset references to read-only paths under the root.

    Each reference's ``relative_path`` is joined under ``dataset_root``, then
    checked for directory escape (``..`` / absolute) and existence. The result
    is a list of ``{dataset_id, path, access}`` entries; the dataset root is the
    shared "all datasets" directory, never one specific dataset. A duplicate
    ``dataset_id`` is rejected so one id can never resolve to two paths.
    """
    root = Path(dataset_root).expanduser().resolve()
    resolved: list[dict] = []
    seen: set[str] = set()
    for ref in refs:
        if ref.dataset_id in seen:
            raise DatasetResolutionError(f"duplicate dataset_id: {ref.dataset_id!r}")
        seen.add(ref.dataset_id)
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


def dataset_env_overrides(
    dataset_root: str | Path, resolved: list[dict]
) -> dict[str, str]:
    """Expose resolved datasets to scripts as a generic ``id -> path`` map.

    No framework is named and no single dataset is preferred: the Experiment
    Agent passes this mapping through so a script can look up the dataset it
    actually needs by id. Model/Hub cache variables are deliberately not set.
    """
    return {
        RESAGENT2_DATASET_ROOT: str(Path(dataset_root).expanduser().resolve()),
        RESAGENT2_DATASETS_JSON: json.dumps(
            {entry["dataset_id"]: entry["path"] for entry in resolved},
            ensure_ascii=False,
        ),
    }


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


def mirror_env_overrides(profile: str) -> dict[str, str]:
    """Return mirror env overrides for a named profile (``none`` is a no-op)."""
    return dict(_MIRROR_PROFILES.get(profile, {}))
