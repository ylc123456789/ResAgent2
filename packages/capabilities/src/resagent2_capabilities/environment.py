"""Content-addressed conda environment provisioning (simple core only)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from .resources import ResourceLayout


class EnvironmentManagerError(ValueError):
    """Raised when an environment cannot be created or bound."""


def project_slug(name: str) -> str:
    """Lowercase to an alphanumeric slug; other characters collapse to dashes."""
    parts: list[str] = []
    last_dash = True
    for char in (name or "").lower():
        if char.isalnum():
            parts.append(char)
            last_dash = False
        elif not last_dash:
            parts.append("-")
            last_dash = True
    return "".join(parts).strip("-") or "project"


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_ENV_FILES = (
    "environment.yml",
    "environment.yaml",
    "conda.yml",
    "conda.yaml",
    "requirements.txt",
    "pyproject.toml",
)


def env_spec(repo_path: Path, python_version: str) -> dict:
    """Return the identity-bearing env spec: python version + hashed dep files."""
    files: dict[str, str] = {}
    for name in _ENV_FILES:
        path = Path(repo_path) / name
        if not path.is_file():
            continue
        try:
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return {"python": python_version, "files": files}


def env_id(project: str, repo_identity: str, spec: dict) -> str:
    """Return ``resenv_<slug>_<sha256(repo identity + env spec)[:12]>``.

    ``repo_identity`` is the caller-provided (repo source + commit) string;
    it never depends on the repository basename alone.
    """
    identity = json.dumps(
        {"repo": repo_identity, "spec": spec},
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"resenv_{project_slug(project)}_{sha256_hex(identity)[:12]}"


def find_conda() -> str | None:
    """Find conda from config, PATH, or common install locations."""
    configured = os.environ.get("RESAGENT2_CONDA_EXE")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("conda")
    if found:
        return found
    for candidate in (
        Path.home() / "miniconda3" / "bin" / "conda",
        Path.home() / "anaconda3" / "bin" / "conda",
        Path("/opt/conda/bin/conda"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _find_environment_yml(repo_path: Path) -> Path | None:
    for name in ("environment.yml", "environment.yaml", "conda.yml", "conda.yaml"):
        candidate = Path(repo_path) / name
        if candidate.is_file():
            return candidate
    return None


class EnvironmentManager:
    """Create or reuse a conda env whose prefix is derived from content identity."""

    def __init__(
        self,
        *,
        env_root: str | Path | None = None,
        conda_exe: str | None = None,
    ) -> None:
        self.env_root = (
            Path(env_root).expanduser()
            if env_root
            else ResourceLayout.from_env().env_root
        ).resolve()
        self.conda_exe = conda_exe or find_conda()

    def prefix(self, identifier: str) -> Path:
        return self.env_root / identifier

    def ensure(
        self,
        *,
        identifier: str,
        repo_path: Path,
        python_version: str,
    ) -> Path:
        """Return the env prefix, creating the environment on first use."""
        if self.conda_exe is None:
            raise EnvironmentManagerError(
                "conda not found; set RESAGENT2_CONDA_EXE or install conda"
            )
        prefix = self.prefix(identifier)
        if prefix.exists():
            return prefix
        prefix.parent.mkdir(parents=True, exist_ok=True)
        env_file = _find_environment_yml(repo_path)
        if env_file is not None:
            command = [
                self.conda_exe,
                "env",
                "create",
                "-p",
                str(prefix),
                "-f",
                str(env_file),
            ]
        else:
            command = [
                self.conda_exe,
                "create",
                "-p",
                str(prefix),
                f"python={python_version}",
                "-y",
            ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise EnvironmentManagerError(
                f"conda env creation failed: {(result.stderr or '').strip()}"
            )
        return prefix
