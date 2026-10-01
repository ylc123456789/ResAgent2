"""Real Bash execution shares the existing process budget and durable receipts."""

from pathlib import Path
import sys
import time

import pytest

from resagent2_components.process import ProcessRunner, UnsafeCommandError, output_tail
from resagent2_components.workspace import WorkspaceBoundary
from resagent2_contracts import WorkspaceAccess, WorkspaceGrant
from resagent2_runtime.budget import execution_budget


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="fixed Linux Bash execution")


@pytest.fixture
def runner(tmp_path):
    return ProcessRunner(WorkspaceBoundary(WorkspaceGrant(
        root=str(tmp_path), source="local",
        access=WorkspaceAccess(read_paths=["."], write_paths=["."]),
    )))


def run_shell(runner, script, **kwargs):
    return runner.run_shell(
        script, log_dir=".resagent2/shell", index=1, timeout_seconds=5, **kwargs,
    )


@pytest.mark.parametrize("script,code,stdout", [
    ("printf 'abc\\n' | tr a-z A-Z", 0, "ABC\n"),
    ("false | cat", 1, ""),
    ("false\nprintf 'continued\\n'", 0, "continued\n"),
])
def test_pipeline_exit_status_without_implicit_errexit(runner, script, code, stdout):
    result = run_shell(runner, script)
    assert result.exit_code == code and not result.timed_out
    assert Path(result.stdout_path).read_text() == stdout


def test_multiline_heredoc_and_receipt_preserve_script_bytes(runner, tmp_path):
    payload = "  unicode: \u4e2d\u6587\n$HOME $(printf changed) `printf changed`\n\ttrailing spaces  \n"
    script = "\ncat <<'PAYLOAD' > payload.txt\n" + payload + "PAYLOAD\n\n"
    result = run_shell(runner, script)
    assert result.exit_code == 0
    assert (tmp_path / "payload.txt").read_bytes() == payload.encode()
    assert result.command == script


def test_workspace_bound_prefix_and_environment_filtering(runner, tmp_path):
    result = run_shell(
        runner,
        "printf '%s\\n' \"$PWD\" \"$RESAGENT_TEST_VALUE\" \"$RESAGENT_PREFIX\" "
        "\"${RESAGENT_TEST_API_KEY-unset}\"",
        argv_prefix=["/usr/bin/env", "RESAGENT_PREFIX=bound"],
        extra_env={"RESAGENT_TEST_VALUE": "available", "RESAGENT_TEST_API_KEY": "hidden"},
    )
    assert result.exit_code == 0
    assert Path(result.stdout_path).read_text().splitlines() == [
        str(tmp_path), "available", "bound", "unset",
    ]


def test_inherited_shell_startup_and_exported_functions_are_not_executed(runner, tmp_path):
    startup = tmp_path / "startup.sh"
    startup.write_text("touch startup-executed\n")
    result = run_shell(
        runner,
        "false\nprintf 'continued\\n'\nif declare -F injected >/dev/null; then injected; fi",
        extra_env={
            "BASH_ENV": str(startup), "ENV": str(startup),
            "SHELLOPTS": "errexit:xtrace", "BASHOPTS": "nullglob",
            "CDPATH": str(tmp_path),
            "BASH_FUNC_injected%%": "() { touch function-executed; }",
        },
    )
    assert result.exit_code == 0
    assert Path(result.stdout_path).read_text() == "continued\n"
    assert Path(result.stderr_path).read_text() == ""
    assert not (tmp_path / "startup-executed").exists()
    assert not (tmp_path / "function-executed").exists()


def test_shell_obeys_shared_deadline_and_keeps_partial_logs(runner, tmp_path):
    started = time.monotonic()
    with execution_budget(max_llm_calls=1, timeout_seconds=0.4):
        result = run_shell(
            runner,
            "printf 'started\\n'; printf 'diagnostic\\n' >&2; "
            "sleep 30 & child=$!; printf '%s' \"$child\" > child.pid; wait \"$child\"",
        )
    assert result.timed_out and result.exit_code != 0
    assert time.monotonic() - started < 3
    assert output_tail(result.stdout_path) == "started\n"
    assert output_tail(result.stderr_path) == "diagnostic\n"
    child = int((tmp_path / "child.pid").read_text())
    status = Path(f"/proc/{child}/stat")
    until = time.monotonic() + 1
    while status.exists() and status.read_text().rsplit(")", 1)[1].split()[0] != "Z":
        assert time.monotonic() < until, "shell child survived the shared deadline"
        time.sleep(0.01)


@pytest.mark.parametrize("script", ["printf hi | cat", "printf hi\nprintf bye", "echo $(pwd)"])
def test_existing_argv_entry_still_rejects_shell_features(runner, script):
    with pytest.raises(UnsafeCommandError):
        runner.run(script, log_dir=".resagent2/plain", index=1, timeout_seconds=5)


def test_shell_rejects_non_linux_before_starting_process(runner, monkeypatch):
    monkeypatch.setattr("resagent2_components.process.sys.platform", "darwin")
    with pytest.raises(RuntimeError, match="only on Linux"):
        run_shell(runner, "printf never")
    assert not (runner.boundary.root / ".resagent2").exists()


@pytest.mark.parametrize("script", [" \n\t", "printf hi\x00"])
def test_shell_rejects_invalid_script_before_starting_process(runner, script):
    with pytest.raises(ValueError, match="nonempty.*NUL"):
        run_shell(runner, script)
    assert not (runner.boundary.root / ".resagent2").exists()


def test_output_tail_reads_only_bounded_suffix(tmp_path, monkeypatch):
    path = tmp_path / "large.log"
    path.write_bytes(b"x" * 100_000 + "\u4e2d\u6587: end\n".encode())
    original_open = Path.open
    reads = []

    class BoundedReader:
        def __enter__(self):
            self.handle = original_open(path, "rb")
            return self

        def __exit__(self, *args):
            self.handle.close()

        def seek(self, *args):
            return self.handle.seek(*args)

        def tell(self):
            return self.handle.tell()

        def read(self, size=-1):
            reads.append(size)
            return self.handle.read(size)

    monkeypatch.setattr(Path, "open", lambda self, *args, **kwargs: BoundedReader())
    assert output_tail(path, limit=8) == "\u4e2d\u6587: end\n"
    assert reads == [32]


def test_output_tail_handles_absent_logs_and_empty_limits(tmp_path):
    assert output_tail(tmp_path / "absent.log") == ""
    assert output_tail(tmp_path, limit=0) == ""
