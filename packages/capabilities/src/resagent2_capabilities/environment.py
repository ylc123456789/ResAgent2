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


def _major_minor(version: str) -> tuple[int, int] | None:
    parts = (version or "").strip().split(".")
    if len(parts) < 2:
        return None
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def version_matches(requested: str, actual: str) -> bool:
    """True when ``actual`` satisfies ``requested`` at the major.minor level.

    ``3.10`` requested accepts ``3.10.16`` actual; ``3.12.x`` fails.
    """
    req = _major_minor(requested)
    act = _major_minor(actual)
    if req is None or act is None:
        return False
    return req == act


@dataclass(frozen=True, slots=True)
class PreparedEnvironment:
    """A bound base environment; the physical prefix is a local detail."""

    env_id: str
    prefix: Path
    python_version: str


_PROBE = (
    "import json, sys, importlib.util; "
    "print(json.dumps({"
    "'sys_executable': sys.executable, "
    "'sys_prefix': sys.prefix, "
    "'python_version': sys.version.split()[0], "
    "'pip_available': importlib.util.find_spec('pip') is not None"
    "}))"
)


class EnvironmentManager:
    """Create, reuse and audit a base Python env bound to run_id + workspace_id.

    The manager never interprets project dependencies: it only creates the base
    Python environment (python + pip) and proves (via ``audit``) that commands
    run inside it with the requested interpreter. Dependency installation is
    the Agent's job, through ``run_setup``.
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
        """Return a structurally healthy, already-created base env, or ``None``.

        This is a cheap structural check (marker + python + pip files exist);
        the deep interpreter probe is ``audit``'s job.
        """
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
        """Create (or recreate) the base env, audited, and return it.

        An existing env is reused only when its *actual* interpreter satisfies
        ``python_version``; otherwise it is deleted (strictly) and recreated.
        The ready marker records the *actual* probed version, never the request.
        """
        if self.conda_exe is None:
            raise EnvironmentManagerError(
                "conda not found; set RESAGENT2_CONDA_EXE or install conda"
            )
        env_id = self.env_id(run_id=run_id, workspace_id=workspace_id)
        prefix = self.prefix(run_id=run_id, workspace_id=workspace_id)
        if prefix.exists():
            probe = self._probe(prefix)
            reusable = (
                probe["returncode"] == 0
                and probe["prefix_match"]
                and probe["pip_available"]
                and version_matches(python_version, probe["python_version"])
            )
            if reusable:
                self._write_marker(prefix, env_id, probe["python_version"])
                return PreparedEnvironment(
                    env_id=env_id,
                    prefix=prefix,
                    python_version=probe["python_version"],
                )
            self._delete_if_managed(prefix)
        self.env_root.mkdir(parents=True, exist_ok=True)
        command = [
            self.conda_exe,
            "create",
            "-p",
            str(prefix),
            f"python={python_version}",
            "pip",
            "-y",
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            raise EnvironmentManagerError(
                f"conda env creation failed: {(result.stderr or '').strip()}"
            )
        environment = PreparedEnvironment(
            env_id=env_id, prefix=prefix, python_version=python_version
        )
        audit = self.audit(environment)
        if not audit["success"]:
            raise EnvironmentManagerError(
                "environment audit failed after create: "
                + (audit.get("stderr_tail", "").strip())
            )
        actual_version = audit["python_version"]
        self._write_marker(prefix, env_id, actual_version)
        return PreparedEnvironment(
            env_id=env_id, prefix=prefix, python_version=actual_version
        )

    def audit(self, environment: PreparedEnvironment) -> dict:
        """Probe the bound env and report whether it is a correct base env.

        Success requires the probe to run, ``sys.prefix`` to match the bound
        prefix, pip to be importable, and the actual interpreter to satisfy the
        environment's recorded Python version.
        """
        if self.conda_exe is None:
            return {
                "success": False,
                "error": "conda not found",
                "prefix_match": False,
                "pip_available": False,
                "version_match": False,
            }
        probe = self._probe(environment.prefix)
        prefix_match = probe["prefix_match"]
        pip_available = probe["pip_available"]
        actual_version = probe["python_version"]
        version_ok = version_matches(environment.python_version, actual_version)
        success = (
            probe["returncode"] == 0
            and prefix_match
            and pip_available
            and version_ok
        )
        return {
            "success": success,
            "sys_executable": probe["sys_executable"],
            "sys_prefix": probe["sys_prefix"],
            "python_version": actual_version,
            "pip_available": pip_available,
            "prefix_match": prefix_match,
            "version_match": version_ok,
            "stderr_tail": probe["stderr_tail"],
        }

    def _probe(self, prefix: Path) -> dict:
        try:
            result = subprocess.run(
                [
                    self.conda_exe,
                    "run",
                    "--no-capture-output",
                    "-p",
                    str(prefix),
                    "python",
                    "-c",
                    _PROBE,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return {
                "returncode": None,
                "error": str(error),
                "sys_executable": "",
                "sys_prefix": "",
                "python_version": "",
                "pip_available": False,
                "prefix_match": False,
                "stderr_tail": str(error),
            }
        data: dict = {}
        if result.returncode == 0:
            try:
                data = json.loads((result.stdout or "").strip())
            except json.JSONDecodeError:
                data = {}
        prefix_match = (
            Path(str(data.get("sys_prefix", ""))).resolve() == prefix.resolve()
        )
        return {
            "returncode": result.returncode,
            "error": None,
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

    def _write_marker(self, prefix: Path, env_id: str, python_version: str) -> None:
        """Atomically persist the ready marker with the *actual* interpreter."""
        marker = prefix / _BASE_MARKER
        temporary = marker.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "python_version": python_version,
                    "env_id": env_id,
                    "prefix": str(prefix),
                }
            ),
            encoding="utf-8",
        )
        os.replace(temporary, marker)

    def _base_healthy(self, prefix: Path) -> bool:
        marker = prefix / _BASE_MARKER
        if not marker.is_file():
            return False
        bin_dir = prefix / "bin"
        has_python = (bin_dir / "python").exists() or (bin_dir / "python3").exists()
        return has_python and (bin_dir / "pip").exists()

    def _delete_if_managed(self, prefix: Path) -> None:
        resolved = prefix.resolve()
        if resolved != self.env_root and self.env_root not in resolved.parents:
            raise EnvironmentManagerError(
                f"refusing to delete prefix outside env_root: {resolved}"
            )
        try:
            shutil.rmtree(resolved)
        except OSError as error:
            raise EnvironmentManagerError(f"failed to delete environment: {error}") from error


class EnvironmentBinding:
    """Shared mutable binding between the three environment tools.

    Holds the current bound environment and its audit state so that
    ``prepare_environment``, ``run_setup``, ``audit_env`` and every command
    runner all operate on the same environment within one task attempt.

    On construction the binding restores an existing healthy environment via
    ``inspect`` (so a resumed session finds its env), but starts uncertified:
    the restored env must be re-audited before any experiment/verification.
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
        self.current: PreparedEnvironment | None = manager.inspect(
            run_id=run_id, workspace_id=workspace_id
        )
        self.certified: bool = False

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
