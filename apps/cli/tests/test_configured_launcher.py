"""Every CLI process reloads the deployment environment, including resumes."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest


LAUNCHER = Path(__file__).resolve().parents[1] / "run-configured.sh"


@pytest.mark.skipif(shutil.which("bash") is None, reason="Bash deployment entry point")
def test_separate_cli_processes_share_config_and_preserve_arguments(tmp_path):
    config = tmp_path / "deployment config.sh"
    trace_dir = tmp_path / "trace directory"
    config.write_text(
        "RESAGENT2_LLM_TRACE_LEVEL=full\n"
        f"RESAGENT2_LLM_TRACE_DIR={shlex.quote(str(trace_dir))}\n",
        encoding="utf-8",
    )
    executable = tmp_path / "resagent2"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "print(json.dumps({"
        "'argv': sys.argv[1:], "
        "'level': os.environ['RESAGENT2_LLM_TRACE_LEVEL'], "
        "'directory': os.environ['RESAGENT2_LLM_TRACE_DIR']}))\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    env = {
        **os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
        "RESAGENT2_LLM_TRACE_LEVEL": "off", "RESAGENT2_LLM_TRACE_DIR": "/stale",
    }
    for command in ("run", "answer", "resume"):
        args = [command, "run_test", "--field", "decision=a value with spaces"]
        result = subprocess.run(
            ["bash", str(LAUNCHER), str(config), *args],
            env=env, capture_output=True, text=True, check=True,
        )
        assert json.loads(result.stdout) == {
            "argv": args, "level": "full", "directory": str(trace_dir),
        }


@pytest.mark.skipif(shutil.which("bash") is None, reason="Bash deployment entry point")
def test_missing_config_does_not_start_cli(tmp_path):
    result = subprocess.run(
        ["bash", str(LAUNCHER), str(tmp_path / "missing.sh"), "run"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "missing.sh" in result.stderr
