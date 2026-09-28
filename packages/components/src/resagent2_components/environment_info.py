"""Read-only environment facts, separate from base-environment certification."""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from resagent2_runtime.budget import DeadlineExceededError

from .process import run_process


# Only stdlib metadata is read: importing an ML framework can initialize devices
# or execute project code. Run in the same conda prefix as experiment commands.
_INFORMATION_PROBE = r"""
import ast
import importlib.metadata
import json
import os
import platform
import sys

result = {
    "sys_executable": sys.executable,
    "sys_prefix": sys.prefix,
    "python_version": platform.python_version(),
    "system": {
        "os": platform.system(),
        "release": platform.release(),
        "architecture": platform.machine(),
        "logical_cpu_count": os.cpu_count(),
    },
    "device_visibility": {
        key: os.environ[key]
        for key in ("CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES",
                    "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES")
        if key in os.environ
    },
}
if hasattr(os, "sched_getaffinity"):
    try:
        result["system"]["cpu_affinity_count"] = len(os.sched_getaffinity(0))
    except OSError:
        pass
try:
    result["system"]["physical_memory_bytes"] = (
        os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    )
except (AttributeError, OSError, ValueError):
    pass
# These are observed cgroup-v2 values, not inferred effective resource limits.
# Other cgroup layouts remain unknown rather than falling back to host capacity.
result["system"]["cgroup_v2"] = {}
for name in ("cpu.max", "memory.max"):
    try:
        with open("/sys/fs/cgroup/" + name) as handle:
            result["system"]["cgroup_v2"][name] = handle.read(200).strip()
    except OSError:
        pass
try:
    packages = sorted(
        ({"name": dist.metadata["Name"], "version": dist.version}
         for dist in importlib.metadata.distributions() if dist.metadata["Name"]),
        key=lambda item: item["name"].lower(),
    )
    result["packages"] = {
        "status": "observed",
        "source": "importlib.metadata in the bound Python",
        "items": packages,
        "count": len(packages),
    }
except Exception as error:
    result["packages"] = {"status": "unavailable", "error": str(error)[:500]}
# PyTorch wheel metadata can omit its CUDA suffix. Its generated version.py
# contains literal build facts; read those without importing torch or its native code.
try:
    distribution = importlib.metadata.distribution("torch")
    version_file = distribution.locate_file("torch/version.py")
    with open(version_file, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    build = {}
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None
        )
        if isinstance(target, ast.Name) and target.id in {"__version__", "cuda", "hip"}:
            if isinstance(node.value, ast.Constant) and (
                node.value.value is None or isinstance(node.value.value, str)
            ):
                build[target.id] = node.value.value
    if "__version__" not in build:
        raise ValueError("No literal PyTorch build version found")
    result["pytorch_build"] = {
        "status": "observed", "source": str(version_file), **build,
        "scope": "Installed build metadata only; import and device execution are not tested.",
    }
except importlib.metadata.PackageNotFoundError:
    result["pytorch_build"] = {"status": "not_installed"}
except Exception as error:
    result["pytorch_build"] = {"status": "unavailable", "error": str(error)[:500]}
print(json.dumps(result))
"""


def _query(command: list[str], *, timeout: int) -> tuple[str | None, dict]:
    """A failed optional observation never changes an execution result or gate."""
    try:
        result = run_process(command, capture_output=True, text=True, timeout=timeout)
    except DeadlineExceededError:
        # Preserve an already-finished setup's receipt. The next normal operation
        # still checks the shared deadline before it executes.
        return None, {"status": "unavailable", "error": "Run deadline exhausted"}
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, {"status": "unavailable", "error": str(error)[:500]}
    if result.returncode:
        return None, {
            "status": "unavailable", "exit_code": result.returncode,
            "error": (result.stderr or result.stdout or "")[-1000:],
        }
    return result.stdout or "", {}


def _nvidia_information() -> dict:
    source = "nvidia-smi -q -x"
    output, error = _query(["nvidia-smi", "-q", "-x"], timeout=5)
    if output is None:
        return {"source": source, **error}
    try:
        root = ET.fromstring(output)
        if root.tag != "nvidia_smi_log":
            raise ValueError("unexpected NVIDIA response")
        devices = root.findall("gpu")
        return {
            "status": "observed", "source": source,
            "driver_version": root.findtext("driver_version"),
            "driver_reported_cuda_version": root.findtext("cuda_version"),
            "devices": [{
                "name": gpu.findtext("product_name"),
                "memory_total": gpu.findtext("fb_memory_usage/total"),
                "memory_free": gpu.findtext("fb_memory_usage/free"),
            } for gpu in devices[:32]],
            "device_count": len(devices),
            "devices_omitted": max(0, len(devices) - 32),
            "scope": (
                "Devices visible to nvidia-smi; framework visibility and execution "
                "are not tested. The reported CUDA version describes driver support, "
                "not an installed Toolkit or framework build."
            ),
        }
    except (ET.ParseError, ValueError) as error:
        return {"status": "unavailable", "source": source, "error": str(error)[:500]}


def inspect_environment(prefix: Path, conda_exe: str | None) -> dict:
    """Observe host devices and bound-environment metadata without selecting policy."""
    snapshot = {
        "observed_at": datetime.now(UTC).isoformat(),
        "prefix": str(prefix),
        "scope": (
            "Last observed facts, not live monitoring or dependency/device validation. "
            "An unavailable probe does not establish that hardware is absent."
        ),
    }
    if conda_exe is None:
        runtime = {"status": "unavailable", "error": "conda not found"}
    else:
        output, runtime = _query([
            conda_exe, "run", "--no-capture-output", "-p", str(prefix),
            "python", "-I", "-c", _INFORMATION_PROBE,
        ], timeout=15)
        if output is not None:
            try:
                data = json.loads(output)
                if not isinstance(data, dict) or Path(data["sys_prefix"]).resolve() != prefix.resolve():
                    raise ValueError("Information probe did not use the bound Python")
                runtime = {"status": "observed", **data}
            except (KeyError, TypeError, ValueError) as error:
                runtime = {"status": "unavailable", "error": str(error)[:500]}
    snapshot["runtime"] = runtime
    snapshot["nvidia"] = _nvidia_information()
    return snapshot


def information_context(snapshot: dict | None) -> dict:
    """Bound package-list size in repeated context; the tool receipt stays complete."""
    if snapshot is None:
        return {
            "status": "not_observed",
            "scope": "Environment preparation, setup or audit will collect current facts.",
        }
    result = dict(snapshot)
    runtime = dict(result["runtime"])
    packages = runtime.get("packages")
    if packages and packages.get("status") == "observed":
        shown = []
        used = 0
        for item in packages["items"]:
            size = len(json.dumps(item, ensure_ascii=False))
            if used + size > 6000:
                break
            shown.append(item)
            used += size
        runtime["packages"] = {
            **packages, "items": shown,
            "omitted_count": len(packages["items"]) - len(shown),
            "scope": "Installed distribution metadata, not import or device checks; full list in the tool receipt.",
        }
    result["runtime"] = runtime
    return result
