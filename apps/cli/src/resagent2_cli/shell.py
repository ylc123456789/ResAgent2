"""Interactive monitoring shell: a thin human-facing REPL over ResearchController.

The shell is an observer of persisted state, not a second control plane. It
builds the production application through ``composition.build_application``,
runs the three blocking controller methods (``create_run`` / ``answer_question``
/ ``run_until_stable``) on a daemon background thread, and renders progress by
polling the atomically-written ``JsonRunStore`` snapshot plus the optional
append-only LLM trace.

Boundaries (by design, not limitation):

- Ctrl-C only stops watching and returns to the prompt. It never cancels a run;
  real cancellation is a future core state-machine capability.
- ``/attach`` only reads and watches persisted state. It does not start a worker;
  a run that is not already executing requires an explicit ``/resume``.
- The live view uses the metadata trace (agent/tool names). Raw request,
  response, and reasoning are available only through ``/trace``.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from resagent2_contracts import RunStatus, UserAnswer

from . import render
from .composition import build_application
from .main import (
    EXIT_COMPLETED,
    _assignment,
    _default_data_root,
    _new_run_id,
    _parser,
    _request_from_args,
    _run_store,
    _specs_for_existing_run,
    _workspace_specs,
)

TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.PAUSED}
POLL_INTERVAL = 0.25
_WS_FLAGS = ("--workspace", "--git", "--python-version")


class _NoExitParser(argparse.ArgumentParser):
    """Argparse parser that raises instead of killing the shell process.

    The one-shot CLI keeps the stock ``sys.exit`` behavior; the shell uses this
    so a malformed ``/run`` returns to the prompt instead of terminating it.
    """

    def error(self, message: str) -> None:
        raise ValueError(message)


class Runner:
    """Run one blocking controller method on a daemon thread."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._done = threading.Event()
        self._result = None
        self._error = None
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def start(self, fn: Callable[[], object]) -> None:
        if self.active:
            raise RuntimeError("a run is already active; wait for it to finish")
        self._done.clear()
        with self._lock:
            self._result = None
            self._error = None

        def _target() -> None:
            try:
                result = fn()
            except Exception as error:  # noqa: BLE001
                with self._lock:
                    self._error = error
            else:
                with self._lock:
                    self._result = result
            finally:
                self._done.set()

        self._thread = threading.Thread(target=_target, daemon=True)
        self._thread.start()

    def outcome(self) -> tuple[object, Exception | None]:
        with self._lock:
            return self._result, self._error


class Display:
    """Minimal ANSI live-block redraw; degrades to plain prints on a non-tty."""

    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stdout
        self._live_lines = 0
        self._last_non_tty_lines: list[str] | None = None

    def _ansi(self) -> bool:
        return self.stream.isatty()

    def render(self, lines: list[str]) -> None:
        ansi = self._ansi()
        if not ansi and lines == self._last_non_tty_lines:
            return
        if ansi and self._live_lines:
            self.stream.write(f"\x1b[{self._live_lines}A")
            self.stream.write("\x1b[J")
        for line in lines:
            self.stream.write(line + "\n")
        self.stream.flush()
        self._live_lines = len(lines) if ansi else 0
        self._last_non_tty_lines = None if ansi else list(lines)

    def clear(self) -> None:
        if self._ansi() and self._live_lines:
            self.stream.write(f"\x1b[{self._live_lines}A")
            self.stream.write("\x1b[J")
            self.stream.flush()
        self._live_lines = 0
        self._last_non_tty_lines = None


class TraceTail:
    """Incrementally read the append-only JSONL LLM trace."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0

    def reset(self) -> None:
        self.offset = self.path.stat().st_size if self.path.exists() else 0

    def new_records(self, run_id: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        size = self.path.stat().st_size
        if size < self.offset:
            self.offset = 0
        if size <= self.offset:
            return []
        with self.path.open(encoding="utf-8") as handle:
            handle.seek(self.offset)
            raw = handle.read()
        self.offset = size
        records: list[dict] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if run_id is not None and record.get("run_id") != run_id:
                continue
            records.append(record)
        return records


def _split_answer_tokens(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Split ``/answer`` tokens into field tokens and workspace flags."""
    fields: list[str] = []
    ws: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _WS_FLAGS:
            ws.append(token)
            if index + 1 < len(tokens):
                ws.append(tokens[index + 1])
            index += 2
        else:
            fields.append(token)
            index += 1
    return fields, ws


def _flag_value(tokens: list[str], flag: str) -> str | None:
    for index, token in enumerate(tokens):
        if token == flag and index + 1 < len(tokens):
            return tokens[index + 1]
    return None


def _reject_shell_data_root(tokens: list[str]) -> None:
    """Keep one interactive session bound to its startup data root."""
    if "--data-root" in tokens:
        raise ValueError(
            "the shell data root is fixed at startup; exit and restart with "
            "resagent2 shell --data-root PATH"
        )


def _answer_values(run, fields: list[str]) -> dict[str, str]:
    question = run.pending_question
    if question is None:
        raise ValueError("this Run has no pending question")
    if len(fields) == 1 and "=" not in fields[0]:
        requested = question.requested_fields
        if len(requested) != 1:
            raise ValueError(
                "single-value shorthand needs exactly one requested field; "
                "use NAME=VALUE"
            )
        return {requested[0]: fields[0]}
    return dict(_assignment(value, label="--field") for value in fields)


class Shell:
    """REPL loop with slash commands over the production controller."""

    def __init__(
        self,
        *,
        data_root: Path,
        application_builder: Callable[..., object],
        store,
        stream=None,
    ) -> None:
        self.data_root = Path(data_root)
        self.application_builder = application_builder
        self.store = store
        self.stream = stream or sys.stdout
        self.runner = Runner()
        self.current_run_id: str | None = None
        self.display = Display(self.stream)

    # -- entry ---------------------------------------------------------------

    def run(self) -> int:
        _setup_readline()
        self.stream.write("ResAgent2 shell. Type /help for commands, /quit to exit.\n")
        while True:
            try:
                raw = input("resagent2> ")
            except EOFError:
                self.stream.write("\n")
                return EXIT_COMPLETED
            except KeyboardInterrupt:
                self.stream.write("\n")
                continue
            if not raw.strip():
                continue
            try:
                if self._dispatch(raw.strip()):
                    return EXIT_COMPLETED
            except KeyboardInterrupt:
                self.display.clear()
                if getattr(self.runner, "active", False):
                    self.stream.write(
                        "Stopped watching; this shell is still running the Run. "
                        "Use /attach to watch it again.\n"
                    )
                else:
                    self.stream.write(
                        "Stopped watching; the Run is still persisted. "
                        "Use /resume to continue.\n"
                    )
            except SystemExit:
                # argparse --help on a slash command; help was already printed.
                continue
            except Exception as error:  # noqa: BLE001
                self.display.clear()
                self.stream.write(f"error: {error}\n")
        return EXIT_COMPLETED

    def _dispatch(self, line: str) -> bool:
        if not line.startswith("/"):
            self.stream.write(
                "Commands: /run /show /answer /resume /artifacts /trace /attach /quit\n"
            )
            return False
        try:
            tokens = shlex.split(line)
        except ValueError as error:
            raise ValueError(f"could not parse command: {error}") from error
        command = tokens[0][1:]
        rest = tokens[1:]
        if command == "help":
            self.stream.write(
                "/run --goal … [--workspace …]   start a new research Run\n"
                "/show <run_id>                  show a persisted Run\n"
                "/attach <run_id>                watch a persisted Run (read-only)\n"
                "/resume <run_id>                continue a paused/running Run\n"
                "/answer <value|name=value>      answer the pending question\n"
                "/artifacts                      list the current Run's artifacts\n"
                "/trace [run_id]                 show raw LLM trace (full level)\n"
                "/quit                           exit the shell\n"
            )
            return False
        handler = getattr(self, f"_cmd_{command}", None)
        if handler is None:
            raise ValueError(f"unknown command /{command}")
        _reject_shell_data_root(rest)
        return handler(rest)

    # -- command handlers ----------------------------------------------------

    def _cmd_quit(self, rest: list[str]) -> bool:
        return True

    def _cmd_run(self, rest: list[str]) -> bool:
        args = _parser(_NoExitParser).parse_args(["run"] + rest)
        run_id = args.run_id or _new_run_id()
        request = _request_from_args(args)
        workspaces = _workspace_specs(args)
        application = self.application_builder(
            data_root=self.data_root,
            workspaces=workspaces,
        )
        self.current_run_id = run_id
        self.runner.start(
            lambda: application.controller.create_run(run_id, request)
        )
        self._watch(run_id)
        return False

    def _cmd_resume(self, rest: list[str]) -> bool:
        args = _parser(_NoExitParser).parse_args(["resume"] + rest)
        run_id = args.run_id
        existing = self.store.load(run_id)
        application = self.application_builder(
            data_root=self.data_root,
            workspaces=_specs_for_existing_run(args, existing),
        )
        self.current_run_id = run_id
        self.runner.start(
            lambda: application.controller.run_until_stable(run_id)
        )
        self._watch(run_id)
        return False

    def _cmd_show(self, rest: list[str]) -> bool:
        run_id = self._require_run_id(rest, "/show")
        run = self.store.load(run_id)
        self.current_run_id = run_id
        for line in render.render_final(run):
            self.stream.write(line + "\n")
        return False

    def _cmd_attach(self, rest: list[str]) -> bool:
        run_id = self._require_run_id(rest, "/attach")
        self.store.load(run_id)
        self.current_run_id = run_id
        self._watch(run_id, wait_for_runner=False)
        return False

    def _cmd_answer(self, rest: list[str]) -> bool:
        run = self._require_current_run("/answer")
        fields, ws = _split_answer_tokens(rest)
        values = _answer_values(run, fields)
        ws_args = SimpleNamespace(
            workspace=_flag_value(ws, "--workspace"),
            git=_flag_value(ws, "--git"),
            python_version=_flag_value(ws, "--python-version"),
        )
        application = self.application_builder(
            data_root=self.data_root,
            workspaces=_specs_for_existing_run(ws_args, run),
        )
        answer = UserAnswer(
            question_id=run.pending_question.id,
            values=values,
            answered_at=datetime.now(UTC),
        )
        self.runner.start(
            lambda: application.controller.answer_question(run.run_id, answer)
        )
        self._watch(run.run_id)
        return False

    def _cmd_artifacts(self, rest: list[str]) -> bool:
        run = self._require_current_run("/artifacts")
        for line in render.render_artifacts(run):
            self.stream.write(line + "\n")
        return False

    def _cmd_trace(self, rest: list[str]) -> bool:
        if len(rest) > 1:
            raise ValueError("/trace accepts at most one run_id")
        run_id = rest[0] if rest else self.current_run_id
        for line in render.render_trace(self._read_trace(run_id)):
            self.stream.write(line + "\n")
        return False

    # -- helpers -------------------------------------------------------------

    def _require_run_id(self, rest: list[str], command: str) -> str:
        if len(rest) != 1:
            raise ValueError(f"{command} needs exactly one run_id")
        return rest[0]

    def _require_current_run(self, command: str):
        if self.current_run_id is None:
            raise ValueError(
                f"{command} needs a current Run; "
                "use /run, /show, /attach, or /resume first"
            )
        return self.store.load(self.current_run_id)

    def _trace_path(self) -> Path | None:
        value = os.environ.get("RESAGENT2_LLM_TRACE_DIR")
        if not value:
            return None
        return Path(value).expanduser() / "llm_traces.jsonl"

    def _read_trace(self, run_id: str | None) -> list[dict]:
        path = self._trace_path()
        if path is None:
            raise ValueError(
                "trace not enabled: set RESAGENT2_LLM_TRACE_DIR "
                "(and RESAGENT2_LLM_TRACE_LEVEL=full)"
            )
        if not path.exists():
            return []
        records: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if run_id is not None and record.get("run_id") != run_id:
                continue
            records.append(record)
        return records

    def _watch(
        self,
        run_id: str,
        *,
        poll_interval: float = POLL_INTERVAL,
        wait_for_runner: bool = True,
    ):
        path = self._trace_path()
        tail = TraceTail(path) if path is not None else None
        if tail is not None:
            tail.reset()
        while True:
            try:
                run = self.store.load(run_id)
            except Exception:  # noqa: BLE001
                run = None
            records = tail.new_records(run_id) if tail is not None else []
            self.display.render(render.render_live(run, records))
            if wait_for_runner and self.runner.done:
                result, error = self.runner.outcome()
                self.display.clear()
                if error is not None:
                    raise RuntimeError(f"run failed: {error}") from error
                if result is not None:
                    for line in render.render_final(result):
                        self.stream.write(line + "\n")
                return result
            if run is not None and run.status in TERMINAL_STATUSES:
                self.display.clear()
                for line in render.render_final(run):
                    self.stream.write(line + "\n")
                return run
            time.sleep(poll_interval)


def _setup_readline() -> None:
    """Enable line editing and a best-effort history file (Linux only)."""
    if not sys.stdin.isatty():
        return
    try:
        import readline  # noqa: F401
    except ImportError:
        return
    history = Path.home() / ".resagent2_history"
    try:
        readline.read_history_file(str(history))
    except (OSError, FileNotFoundError):
        pass

    def _save() -> None:
        try:
            readline.write_history_file(str(history))
        except OSError:
            pass

    import atexit

    atexit.register(_save)


def run_shell(
    *,
    data_root: str | Path | None = None,
    application_builder: Callable[..., object] = build_application,
    store_factory: Callable[..., object] = _run_store,
    stream=None,
) -> int:
    root = Path(data_root or _default_data_root()).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    store = store_factory(root)
    shell = Shell(
        data_root=root,
        application_builder=application_builder,
        store=store,
        stream=stream,
    )
    return shell.run()
