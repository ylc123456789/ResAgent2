"""Environment facts remain optional observations of the bound interpreter."""

from __future__ import annotations

import builtins
import io
import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from resagent2_components import environment_info as module


NVIDIA_XML = """<nvidia_smi_log>
<driver_version>570.124.04</driver_version><cuda_version>12.8</cuda_version>
<gpu><product_name>NVIDIA GeForce RTX 4090</product_name>
<fb_memory_usage><total>24564 MiB</total><free>24000 MiB</free></fb_memory_usage>
</gpu></nvidia_smi_log>"""


@pytest.fixture(autouse=True)
def no_real_pytorch_build(monkeypatch):
    """Probe tests must not inspect any framework installed on the test host."""
    import importlib.metadata

    original = importlib.metadata.distribution
    def distribution(name):
        if name == "torch":
            raise importlib.metadata.PackageNotFoundError(name)
        return original(name)
    monkeypatch.setattr(importlib.metadata, "distribution", distribution)


def _torch_distribution(monkeypatch, tmp_path, version_source):
    import importlib.metadata
    from types import SimpleNamespace

    version_file = tmp_path / "torch" / "version.py"
    version_file.parent.mkdir()
    version_file.write_text(version_source, encoding="utf-8")
    distribution = SimpleNamespace(
        metadata={"Name": "torch"}, version="2.14.0",
        locate_file=lambda name: tmp_path / name,
    )
    monkeypatch.setattr(importlib.metadata, "distributions", lambda: [distribution])
    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: distribution)
    return version_file


def _runtime(prefix: Path, *, count: int = 1) -> dict:
    items = [{"name": f"package-{index:04d}", "version": "1.2.3"} for index in range(count)]
    return {
        "sys_prefix": str(prefix),
        "sys_executable": str(prefix / "bin/python"),
        "python_version": "3.12.13",
        "system": {"os": "Linux", "logical_cpu_count": 32},
        "device_visibility": {"CUDA_VISIBLE_DEVICES": "0"},
        "packages": {"status": "observed", "items": items, "count": count},
    }


def _install_responses(monkeypatch, prefix, *, runtime=None, nvidia=NVIDIA_XML):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        output = nvidia if command[0] == "nvidia-smi" else json.dumps(
            runtime if runtime is not None else _runtime(prefix)
        )
        if isinstance(output, Exception):
            raise output
        if isinstance(output, subprocess.CompletedProcess):
            return output
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
    monkeypatch.setattr(module, "run_process", run)
    return calls


def test_facts_are_collected_from_bound_python_and_driver(monkeypatch, tmp_path):
    calls = _install_responses(monkeypatch, tmp_path)
    snapshot = module.inspect_environment(tmp_path, "/opt/conda/bin/conda")

    assert snapshot["runtime"]["sys_prefix"] == str(tmp_path)
    assert snapshot["runtime"]["device_visibility"] == {"CUDA_VISIBLE_DEVICES": "0"}
    assert snapshot["nvidia"]["driver_version"] == "570.124.04"
    assert snapshot["nvidia"]["driver_reported_cuda_version"] == "12.8"
    assert snapshot["nvidia"]["device_count"] == 1
    assert snapshot["nvidia"]["devices"][0]["name"] == "NVIDIA GeForce RTX 4090"
    assert snapshot["nvidia"]["devices"][0]["memory_total"] == "24564 MiB"
    assert datetime.fromisoformat(snapshot["observed_at"]).tzinfo is not None
    command, options = calls[0]
    assert command[:6] == [
        "/opt/conda/bin/conda", "run", "--no-capture-output", "-p", str(tmp_path), "python",
    ]
    assert command[6:8] == ["-I", "-c"]
    assert options["timeout"] <= 15
    assert calls[1][0] == ["nvidia-smi", "-q", "-x"]
    assert calls[1][1]["timeout"] <= 5


@pytest.mark.parametrize("failure", [
    FileNotFoundError("nvidia-smi is not installed"),
    subprocess.TimeoutExpired(["nvidia-smi"], timeout=5),
    subprocess.CompletedProcess(["nvidia-smi"], 9, stdout="", stderr="driver unavailable"),
    "<not-valid-xml",
    "<unrelated/>",
])
def test_failed_device_probe_preserves_runtime_and_does_not_claim_no_gpu(
    monkeypatch, tmp_path, failure,
):
    _install_responses(monkeypatch, tmp_path, nvidia=failure)
    snapshot = module.inspect_environment(tmp_path, "conda")

    assert snapshot["runtime"]["status"] == "observed"
    assert snapshot["nvidia"]["status"] == "unavailable"
    assert snapshot["nvidia"]["error"]
    assert "device_count" not in snapshot["nvidia"]
    assert "devices" not in snapshot["nvidia"]


def test_observed_zero_devices_differs_from_failed_probe(monkeypatch, tmp_path):
    _install_responses(
        monkeypatch, tmp_path,
        nvidia="<nvidia_smi_log><driver_version>570.124.04</driver_version></nvidia_smi_log>",
    )
    snapshot = module.inspect_environment(tmp_path, "conda")
    assert snapshot["nvidia"]["status"] == "observed"
    assert snapshot["nvidia"]["device_count"] == 0
    assert snapshot["nvidia"]["devices"] == []


def test_wrong_interpreter_metadata_is_not_presented_as_bound_facts(monkeypatch, tmp_path):
    _install_responses(monkeypatch, tmp_path, runtime=_runtime(tmp_path / "other"))
    snapshot = module.inspect_environment(tmp_path, "conda")

    assert snapshot["runtime"]["status"] == "unavailable"
    assert "bound Python" in snapshot["runtime"]["error"]
    assert "packages" not in snapshot["runtime"]
    assert snapshot["nvidia"]["status"] == "observed"


def test_missing_conda_still_reports_host_devices(monkeypatch, tmp_path):
    calls = _install_responses(monkeypatch, tmp_path)
    snapshot = module.inspect_environment(tmp_path, None)

    assert snapshot["runtime"]["status"] == "unavailable"
    assert snapshot["nvidia"]["device_count"] == 1
    assert [command[0] for command, _ in calls] == ["nvidia-smi"]


def test_probe_reads_distribution_metadata_without_importing_frameworks(
    monkeypatch, capsys,
):
    import importlib.metadata
    from types import SimpleNamespace

    monkeypatch.setattr(
        importlib.metadata, "distributions",
        lambda: [
            SimpleNamespace(metadata={"Name": "torch"}, version="2.14.0+cu130"),
            SimpleNamespace(metadata={"Name": "numpy"}, version="2.4.0"),
        ],
    )
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in {"torch", "tensorflow", "numpy"}:
            raise AssertionError(f"Environment metadata must not import {name}")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    original_open = builtins.open
    def cgroup_open(path, *args, **kwargs):
        if str(path) in {"/sys/fs/cgroup/cpu.max", "/sys/fs/cgroup/memory.max"}:
            return io.StringIO("max\n")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", cgroup_open)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
    monkeypatch.setenv("PRIVATE_API_KEY", "must-not-be-copied")

    exec(module._INFORMATION_PROBE, {})
    observed = json.loads(capsys.readouterr().out)

    assert observed["packages"]["items"] == [
        {"name": "numpy", "version": "2.4.0"},
        {"name": "torch", "version": "2.14.0+cu130"},
    ]
    assert observed["device_visibility"]["CUDA_VISIBLE_DEVICES"] == "1"
    assert "PRIVATE_API_KEY" not in observed["device_visibility"]
    assert "must-not-be-copied" not in json.dumps(observed)


def test_package_metadata_error_preserves_python_and_system_facts(monkeypatch, capsys):
    import importlib.metadata

    def broken_metadata():
        raise ValueError("damaged package metadata")
    monkeypatch.setattr(importlib.metadata, "distributions", broken_metadata)

    exec(module._INFORMATION_PROBE, {})
    observed = json.loads(capsys.readouterr().out)

    assert observed["sys_prefix"]
    assert observed["system"]["os"]
    assert observed["packages"]["status"] == "unavailable"
    assert "damaged package metadata" in observed["packages"]["error"]


def test_context_bounds_package_list_without_mutating_complete_receipt(tmp_path):
    snapshot = {
        "observed_at": "2026-09-28T00:00:00+00:00",
        "prefix": str(tmp_path),
        "runtime": {"status": "observed", **_runtime(tmp_path, count=1000)},
        "nvidia": {"status": "unavailable", "error": "not installed"},
    }
    before = json.dumps(snapshot, sort_keys=True)

    rendered = module.information_context(snapshot)
    packages = rendered["runtime"]["packages"]

    assert 0 < len(packages["items"]) < 1000
    assert packages["omitted_count"] == 1000 - len(packages["items"])
    assert packages["count"] == 1000
    assert sum(len(json.dumps(item, ensure_ascii=False)) for item in packages["items"]) <= 6000
    assert json.dumps(snapshot, sort_keys=True) == before
    assert len(snapshot["runtime"]["packages"]["items"]) == 1000
    assert module.information_context(None)["status"] == "not_observed"



@pytest.mark.parametrize("deadline_before_runtime", [False, True])
def test_optional_information_deadline_preserves_completed_base_audit(
    monkeypatch, tmp_path, deadline_before_runtime,
):
    from types import SimpleNamespace

    from resagent2_components import EnvironmentBinding, PreparedEnvironment
    from resagent2_runtime.budget import DeadlineExceededError

    current = PreparedEnvironment(
        env_id="environment_test", prefix=tmp_path, python_version="3.12.13",
    )
    manager = SimpleNamespace(
        conda_exe="conda",
        inspect=lambda **_: current,
        audit=lambda _: {
            "success": True, "sys_prefix": str(tmp_path),
            "python_version": "3.12.13", "pip_available": True,
        },
    )
    def run(command, **kwargs):
        if deadline_before_runtime or command[0] == "nvidia-smi":
            raise DeadlineExceededError("Run deadline exhausted")
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(_runtime(tmp_path)), stderr="",
        )
    monkeypatch.setattr(module, "run_process", run)
    monkeypatch.setattr(
        "resagent2_components.environment.inspect_environment", module.inspect_environment,
    )
    binding = EnvironmentBinding(manager, run_id="run_test", workspace_id="workspace_test")

    audit = binding.audit()

    # Optional post-operation facts must not erase the operation's actual result.
    # Later operations still enforce the existing shared deadline independently.
    assert audit["success"] is True
    assert audit["sys_prefix"] == str(tmp_path)
    assert binding.certified is True
    information = audit["environment_information"]
    assert information["nvidia"]["status"] == "unavailable"
    assert "deadline" in information["nvidia"]["error"].lower()
    assert information["runtime"]["status"] == (
        "unavailable" if deadline_before_runtime else "observed"
    )
    if not deadline_before_runtime:
        assert information["runtime"]["packages"]["count"] == 1



def test_pytorch_build_recovers_cuda_suffix_without_executing_module(
    monkeypatch, tmp_path, capsys,
):
    source = (
        'raise RuntimeError("version file must never execute")\n'
        '__version__: str = "2.14.0+cu130"\n'
        'cuda: str = "13.0"\n'
        'hip = None\n'
    )
    version_file = _torch_distribution(monkeypatch, tmp_path, source)

    exec(module._INFORMATION_PROBE, {})
    observed = json.loads(capsys.readouterr().out)

    assert observed["packages"]["items"] == [{"name": "torch", "version": "2.14.0"}]
    assert observed["pytorch_build"]["status"] == "observed"
    assert observed["pytorch_build"]["__version__"] == "2.14.0+cu130"
    assert observed["pytorch_build"]["cuda"] == "13.0"
    assert observed["pytorch_build"]["hip"] is None
    assert observed["pytorch_build"]["source"] == str(version_file)


def test_pytorch_not_installed_is_explicit_without_affecting_base_facts(
    monkeypatch, capsys,
):
    import importlib.metadata
    monkeypatch.setattr(importlib.metadata, "distributions", lambda: [])

    exec(module._INFORMATION_PROBE, {})
    observed = json.loads(capsys.readouterr().out)

    assert observed["pytorch_build"] == {"status": "not_installed"}
    assert observed["packages"]["status"] == "observed"
    assert observed["packages"]["count"] == 0
    assert observed["sys_prefix"]
    assert observed["system"]["os"]


@pytest.mark.parametrize("invalid_source", [
    "__version__ = (",
    '__version__ = str("2.14.0+cu130")\ncuda = "13.0"\n',
])
def test_unreadable_build_facts_do_not_execute_code_or_hide_other_facts(
    monkeypatch, tmp_path, capsys, invalid_source,
):
    marker = tmp_path / "must_not_be_created"
    source = (
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n"
        + invalid_source
    )
    _torch_distribution(monkeypatch, tmp_path, source)

    exec(module._INFORMATION_PROBE, {})
    observed = json.loads(capsys.readouterr().out)

    assert not marker.exists()
    assert observed["pytorch_build"]["status"] == "unavailable"
    assert observed["pytorch_build"]["error"]
    assert observed["packages"]["items"] == [{"name": "torch", "version": "2.14.0"}]
    assert observed["sys_prefix"]
    assert observed["system"]["os"]
