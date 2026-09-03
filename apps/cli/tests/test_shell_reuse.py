"""The shell and the one-shot CLI must share the same flag -> request building."""

from __future__ import annotations

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
    assert request.budget.max_tasks == 3
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
