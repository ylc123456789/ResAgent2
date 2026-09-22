"""The shell and the one-shot CLI must share the same flag -> request building."""

from __future__ import annotations

import io
import json
import shlex

import pytest

from resagent2_cli.main import _parser, _request_from_args, _workspace_specs, cli
from resagent2_cli.shell import _NoExitParser
from resagent2_orchestrator import InMemoryRunStore


def test_request_from_args_builds_request(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    args = _parser().parse_args(
        ["run", "--goal", "g", "--workspace", str(workspace), "--max-tasks", "3"]
    )
    request = _request_from_args(args)
    assert request.goal == "g"
    assert request.execution_limits.max_tasks == 3
    specs = _workspace_specs(args)
    assert specs["ws_main"].location == str(workspace.resolve())


def test_shell_reuses_run_subparser():
    args = _parser(_NoExitParser).parse_args(
        ["run", "--goal", "g", "--hypothesis", "h", "--max-llm-calls", "5"]
    )
    request = _request_from_args(args)
    assert request.goal == "g"
    assert request.hypothesis == "h"
    assert request.budget.max_llm_calls == 5


def test_cli_shell_subcommand_routes_to_shell(monkeypatch, tmp_path):
    monkeypatch.setattr("builtins.input", lambda *a: "/quit")
    code = cli(
        ["shell", "--data-root", str(tmp_path)],
        application_builder=lambda **kwargs: None,
        store_factory=lambda root: InMemoryRunStore(),
    )
    assert code == 0


def test_cli_no_arguments_routes_to_shell(monkeypatch, tmp_path):
    monkeypatch.setattr("builtins.input", lambda *a: "/quit")
    code = cli(
        [],
        application_builder=lambda **kwargs: None,
        store_factory=lambda root: InMemoryRunStore(),
    )
    assert code == 0


@pytest.mark.parametrize("answer_tokens", [
    ["accuracy"], ["primary_metric=accuracy"], ["--field", "primary_metric=accuracy"],
])
@pytest.mark.parametrize("repeat_workspace", [False, True])
def test_shell_answer_reaches_controller_and_resumes_same_session(
    tmp_path, monkeypatch, answer_tokens, repeat_workspace,
):
    from resagent2_cli import composition
    from resagent2_cli.shell import Shell
    from resagent2_contracts import RecordedAnswer, RunStatus, SessionStatus
    from resagent2_orchestrator.handoffs import read_json
    from resagent2_runtime import ScriptedLLMClient

    actions = [
        {"tool": "ask_user", "arguments": {
            "assessment": {"statement": "Need the primary metric"},
            "text": "Which metric should be primary?", "requested_fields": ["primary_metric"],
        }},
        {"tool": "finish", "arguments": {
            "report": "Metric recorded; no empirical conclusion yet.",
            "artifacts": [{"kind": "scientific_opinion", "path": "opinion.json",
                           "media_type": "application/json", "summary": "Conclusion",
                           "content": json.dumps({"verdict": "inconclusive", "statement": "Need empirical evidence"})}],
        }},
    ]
    monkeypatch.setattr(composition, "_client", lambda: ScriptedLLMClient(actions))
    args = _parser().parse_args([
        "run", "--goal", "Choose a metric", "--workspace", str(tmp_path), "--read-only",
    ])
    specs = _workspace_specs(args)
    app = composition.build_application(data_root=tmp_path / "data", workspaces=specs)
    paused = app.controller.create_run("run_shell_answer", _request_from_args(args))
    assert paused.status == RunStatus.PAUSED
    builds = []

    def builder(**kwargs):
        builds.append(kwargs)
        return app

    shell = Shell(data_root=tmp_path / "data", application_builder=builder,
                  store=app.run_store, stream=io.StringIO())
    shell.current_run_id = paused.run_id
    workspace_flags = ["--workspace", str(tmp_path), "--read-only"] if repeat_workspace else []
    shell._dispatch(shlex.join(["/answer", *answer_tokens, *workspace_flags]))
    shell.runner._thread.join(timeout=5)
    result, error = shell.runner.outcome()
    assert error is None
    assert result.status == RunStatus.COMPLETED, result.terminal_error
    saved = app.run_store.load(paused.run_id)
    assert saved.scientific_session.id == paused.scientific_session.id
    assert saved.scientific_session.status == SessionStatus.COMPLETED
    assert saved.answers[0].question_text == paused.pending_question.text
    assert saved.answers[0].values == {"primary_metric": "accuracy"}
    assert saved.pending_question is None
    assert saved.workspaces == paused.workspaces
    assert builds[0]["workspaces"] == specs
    answer_refs = [ref for ref in saved.artifacts.values() if ref.kind == "answer"]
    assert len(answer_refs) == 1
    assert read_json(answer_refs[0], RecordedAnswer).question_text == paused.pending_question.text
