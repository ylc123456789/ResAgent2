"""Shared Run deadlines cancel both framework commands and their descendants."""
import os
from pathlib import Path
import shlex
import sys
import time

import pytest

from resagent2_components.process import ProcessRunner, run_process
from resagent2_components.workspace import WorkspaceBoundary
from resagent2_contracts import WorkspaceAccess, WorkspaceGrant
from resagent2_runtime.budget import DeadlineExceededError, execution_budget


@pytest.mark.skipif(os.name != "posix", reason="process group cleanup is POSIX")
@pytest.mark.parametrize("framework", [False, True])
def test_run_deadline_terminates_child_tree(tmp_path, framework):
    script = tmp_path / "parent.py"
    script.write_text(
        "import subprocess,sys,time\n"
        "from pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)\n"
        "Path('child.pid').write_text(str(child.pid))\n"
        "time.sleep(30)\n"
    )
    boundary = WorkspaceBoundary(WorkspaceGrant(
        root=str(tmp_path), source="local",
        access=WorkspaceAccess(read_paths=["."], write_paths=["."]),
    ))
    started = time.monotonic()
    with execution_budget(max_llm_calls=1, timeout_seconds=0.6):
        if framework:
            with pytest.raises(DeadlineExceededError):
                run_process([sys.executable, str(script)], cwd=tmp_path, timeout=30)
        else:
            result = ProcessRunner(boundary).run(
                f"{shlex.quote(sys.executable)} parent.py", log_dir=".resagent2/log",
                index=1, timeout_seconds=30,
            )
            assert result.timed_out and result.exit_code != 0
    assert time.monotonic() - started < 3
    pid = int((tmp_path / "child.pid").read_text())
    stat = Path(f"/proc/{pid}/stat")
    until = time.monotonic() + 1
    while stat.exists() and stat.read_text().rsplit(")", 1)[1].split()[0] != "Z":
        assert time.monotonic() < until, "child survived the Run deadline"
        time.sleep(0.01)


def test_second_operation_cannot_start_after_shared_deadline(tmp_path):
    clock = [0.0]
    with execution_budget(max_llm_calls=1, timeout_seconds=5, clock=lambda: clock[0]):
        run_process([sys.executable, "-c", "pass"])
        clock[0] = 5.0
        marker = tmp_path / "should-not-exist"
        with pytest.raises(DeadlineExceededError):
            run_process([sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"])
    assert not marker.exists()

