"""Shell loop, Runner, and watch polling — deterministic, no LLM, no terminal."""

from __future__ import annotations

import io
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from resagent2_cli.shell import Display, Runner, Shell
from resagent2_contracts import ResearchRequest, RunBudget, RunStatus
from resagent2_orchestrator import InMemoryRunStore, ResearchRun


def _run(status):
    now = datetime.now(UTC)
    return ResearchRun(
        run_id="run_x",
        request=ResearchRequest(
            goal="g",
            budget=RunBudget(
                max_tasks=2,
                max_attempts_per_task=1,
                max_llm_calls=10,
                timeout_seconds=60,
            ),
        ),
        status=status,
        created_at=now,
        updated_at=now,
    )


class _SequenceStore:
    def __init__(self, snapshots):
        self._snapshots = list(snapshots)
        self._index = -1

    def load(self, run_id):
        self._index = min(self._index + 1, len(self._snapshots) - 1)
        return self._snapshots[self._index]

    def save(self, run):
        pass

    def exists(self, run_id):
        return True


class _FakeDisplay:
    def __init__(self):
        self.rendered = []
        self.cleared = 0

    def render(self, lines):
        self.rendered.append(lines)

    def clear(self):
        self.cleared += 1


def _shell(tmp_path, store):
    return Shell(
        data_root=tmp_path,
        application_builder=lambda **kwargs: None,
        store=store,
        stream=io.StringIO(),
    )


# -- Runner ----------------------------------------------------------------


def test_runner_returns_result():
    runner = Runner()
    runner.start(lambda: 42)
    runner._thread.join(timeout=2)
    result, error = runner.outcome()
    assert result == 42 and error is None
    assert runner.done and not runner.active


def test_runner_captures_error():
    runner = Runner()

    def boom():
        raise RuntimeError("boom")

    runner.start(boom)
    runner._thread.join(timeout=2)
    result, error = runner.outcome()
    assert result is None and isinstance(error, RuntimeError)


def test_runner_rejects_concurrent_start():
    runner = Runner()
    runner.start(lambda: time.sleep(0.05))
    with pytest.raises(RuntimeError):
        runner.start(lambda: None)
    runner._thread.join(timeout=2)


# -- watch -----------------------------------------------------------------


def test_watch_stops_on_terminal_snapshot(tmp_path):
    store = _SequenceStore([_run(RunStatus.RUNNING), _run(RunStatus.COMPLETED)])
    shell = _shell(tmp_path, store)
    shell.runner = SimpleNamespace(done=False)
    shell.display = _FakeDisplay()
    result = shell._watch("run_x", poll_interval=0)
    assert result.status == RunStatus.COMPLETED
    assert shell.display.cleared >= 1


def test_watch_returns_result_when_runner_done(tmp_path):
    completed = _run(RunStatus.COMPLETED)
    store = _SequenceStore([_run(RunStatus.RUNNING)])
    shell = _shell(tmp_path, store)
    shell.runner = SimpleNamespace(done=True, outcome=lambda: (completed, None))
    shell.display = _FakeDisplay()
    result = shell._watch("run_x", poll_interval=0)
    assert result is completed


def test_watch_raises_on_runner_error(tmp_path):
    store = _SequenceStore([_run(RunStatus.RUNNING)])
    shell = _shell(tmp_path, store)
    shell.runner = SimpleNamespace(
        done=True, outcome=lambda: (None, RuntimeError("boom"))
    )
    shell.display = _FakeDisplay()
    with pytest.raises(RuntimeError):
        shell._watch("run_x", poll_interval=0)


# -- dispatch loop ---------------------------------------------------------


def test_shell_run_show_and_quit(monkeypatch, tmp_path):
    store = InMemoryRunStore()
    store.save(_run(RunStatus.COMPLETED))
    shell = _shell(tmp_path, store)
    lines = iter(["/show run_x", "/quit"])
    monkeypatch.setattr("builtins.input", lambda *a: next(lines))
    assert shell.run() == 0
    assert "Status: completed" in shell.stream.getvalue()


def test_shell_run_answer_without_current_run_errors(monkeypatch, tmp_path):
    shell = _shell(tmp_path, InMemoryRunStore())
    lines = iter(["/answer accuracy", "/quit"])
    monkeypatch.setattr("builtins.input", lambda *a: next(lines))
    shell.run()
    assert "needs a current Run" in shell.stream.getvalue()


def test_shell_run_unknown_command_errors(monkeypatch, tmp_path):
    shell = _shell(tmp_path, InMemoryRunStore())
    lines = iter(["/bogus", "/quit"])
    monkeypatch.setattr("builtins.input", lambda *a: next(lines))
    shell.run()
    assert "unknown command /bogus" in shell.stream.getvalue()


def test_attach_watches_persisted_state_without_waiting_for_runner(tmp_path):
    store = _SequenceStore([_run(RunStatus.RUNNING), _run(RunStatus.RUNNING), _run(RunStatus.COMPLETED)])
    shell = _shell(tmp_path, store)
    shell.runner = SimpleNamespace(
        done=True,
        outcome=lambda: (_ for _ in ()).throw(AssertionError("not a shell runner")),
    )
    shell.display = _FakeDisplay()

    shell._cmd_attach(["run_x"])

    assert shell.current_run_id == "run_x"
    assert len(shell.display.rendered) == 2


def test_shell_rejects_per_command_data_root(tmp_path):
    shell = _shell(tmp_path, InMemoryRunStore())
    with pytest.raises(ValueError, match="fixed at startup"):
        shell._dispatch("/run --goal g --data-root /other")


def test_display_deduplicates_unchanged_non_tty_blocks():
    stream = io.StringIO()
    display = Display(stream)

    display.render(["one"])
    display.render(["one"])
    display.render(["two"])

    assert stream.getvalue() == "one\ntwo\n"
