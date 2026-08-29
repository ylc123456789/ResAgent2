"""Base-environment lifecycle bound to run_id + workspace_id."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .resources import ResourceLayout


class EnvironmentManagerError(ValueError):
    """Raised when a base environment cannot be created, audited or bound."""


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


_BASE_MARKER = ".resagent2_base_ready"


@dataclass(frozen=True, slots=True)
class PreparedEnvironment:
    """A bound base environment; the physical prefix is a local detail."""

    env_id: str
    prefix: Path
    python_version: str


class EnvironmentManager:
    """Create, reuse and audit a base Python env bound to run_id + workspace_id.

    The manager never interprets project dependencies: it only creates the base
    Python environment and proves (via ``audit``) that commands run inside it.
    Dependency installation is the Agent's job, through ``run_setup``.
    """

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

    def env_id(self, *, run_id: str, workspace_id: str) -> str:
        """Hash-derived, opaque id unique to one (run, workspace) pair."""
        return f"resenv_{sha256_hex(run_id + chr(0) + workspace_id)[:12]}"

    def prefix(self, *, run_id: str, workspace_id: str) -> Path:
        return self.env_root / self.env_id(run_id=run_id, workspace_id=workspace_id)

    def inspect(self, *, run_id: str, workspace_id: str) -> PreparedEnvironment | None:
        """Return a healthy, already-created base env, or ``None``."""
        if self.conda_exe is None:
            return None
        prefix = self.prefix(run_id=run_id, workspace_id=workspace_id)
        if not self._base_healthy(prefix):
            return None
        python_version = self._marker_python(prefix)
        if not python_version:
            return None
        return PreparedEnvironment(
            env_id=self.env_id(run_id=run_id, workspace_id=workspace_id),
            prefix=prefix,
            python_version=python_version,
        )

    def prepare(
        self,
        *,
        run_id: str,
        workspace_id: str,
        python_version: str,
    ) -> PreparedEnvironment:
        """Create (or recreate) the base env and write the ready marker.

        A partial base env is confirmed to live under ``env_root``, deleted, and
        recreated; it is never silently reused as complete.
        """
        if self.conda_exe is None:
            raise EnvironmentManagerError(
                "conda not found; set RESAGENT2_CONDA_EXE or install conda"
            )
        prefix = self.prefix(run_id=run_id, workspace_id=workspace_id)
        if prefix.exists() and not self._base_healthy(prefix):
            self._delete_if_managed(prefix)
        if not prefix.exists():
            self.env_root.mkdir(parents=True, exist_ok=True)
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
        env_id = self.env_id(run_id=run_id, workspace_id=workspace_id)
        marker = prefix / _BASE_MARKER
        marker.write_text(
            json.dumps(
                {
                    "python_version": python_version,
                    "env_id": env_id,
                    "prefix": str(prefix),
                }
            ),
            encoding="utf-8",
        )
        return PreparedEnvironment(
            env_id=env_id, prefix=prefix, python_version=python_version
        )

    def audit(self, environment: PreparedEnvironment) -> dict:
        """Probe the bound env and report whether commands run inside it.

        Proves the base environment is correct (sys.executable / sys.prefix /
        Python version / pip), not that project dependencies are complete.
        """
        if self.conda_exe is None:
            return {"success": False, "error": "conda not found", "prefix_match": False}
        probe = (
            "import json, sys, importlib.util; "
            "print(json.dumps({"
            "'sys_executable': sys.executable, "
            "'sys_prefix': sys.prefix, "
            "'python_version': sys.version.split()[0], "
            "'pip_available': importlib.util.find_spec('pip') is not None"
            "}))"
        )
        try:
            result = subprocess.run(
                [
                    self.conda_exe,
                    "run",
                    "--no-capture-output",
                    "-p",
                    str(environment.prefix),
                    "python",
                    "-c",
                    probe,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return {"success": False, "error": str(error), "prefix_match": False}
        data: dict = {}
        if result.returncode == 0:
            try:
                data = json.loads((result.stdout or "").strip())
            except json.JSONDecodeError:
                data = {}
        prefix_match = (
            Path(str(data.get("sys_prefix", ""))).resolve()
            == environment.prefix.resolve()
        )
        return {
            "success": result.returncode == 0 and prefix_match,
            "sys_executable": data.get("sys_executable", ""),
            "sys_prefix": data.get("sys_prefix", ""),
            "python_version": data.get("python_version", ""),
            "pip_available": bool(data.get("pip_available")),
            "prefix_match": prefix_match,
            "stderr_tail": (result.stderr or "")[-2000:],
        }

    def _marker_python(self, prefix: Path) -> str:
        marker = prefix / _BASE_MARKER
        try:
            info = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        return str(info.get("python_version", "") or "")

    def _base_healthy(self, prefix: Path) -> bool:
        marker = prefix / _BASE_MARKER
        if not marker.is_file():
            return False
        bin_dir = prefix / "bin"
        return (bin_dir / "python").exists() or (bin_dir / "python3").exists()

    def _delete_if_managed(self, prefix: Path) -> None:
        resolved = prefix.resolve()
        if resolved != self.env_root and self.env_root not in resolved.parents:
            raise EnvironmentManagerError(
                f"refusing to delete prefix outside env_root: {resolved}"
            )
        shutil.rmtree(resolved, ignore_errors=True)


class EnvironmentBinding:
    """Shared mutable binding between the three environment tools.

    Holds the current bound environment and its audit state so that
    ``prepare_environment``, ``run_setup``, ``audit_env`` and every command
    runner all operate on the same environment within one task attempt.
    """

    def __init__(
        self,
        manager: EnvironmentManager,
        *,
        run_id: str,
        workspace_id: str,
        hard_constraint: str | None = None,
    ) -> None:
        self.manager = manager
        self.run_id = run_id
        self.workspace_id = workspace_id
        self.hard_constraint = hard_constraint
        self.current: PreparedEnvironment | None = None
        self.certified: bool = False
        self.version_switches: int = 0

    def argv_prefix(self) -> list[str] | None:
        if self.current is None or self.manager.conda_exe is None:
            return None
        return [
            self.manager.conda_exe,
            "run",
            "--no-capture-output",
            "-p",
            str(self.current.prefix),
        ]
