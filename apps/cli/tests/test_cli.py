from __future__ import annotations

from resagent2_contracts import WorkspaceAccess, RunPermissions, ExecutionLimits

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from resagent2_cli import composition
from resagent2_cli.composition import CliApplication, build_application
from resagent2_cli.main import EXIT_COMPLETED, EXIT_PAUSED, cli
from resagent2_contracts import (
    WorkflowAgentKind,
    DatasetRef,
    EnvironmentSpec,
    PendingQuestion,
    ResearchRequest,
    RunBudget,
    RunStatus,
    WorkspaceRecord,
    WorkspaceSourceKind,
    WorkspaceSpec,
)
from resagent2_orchestrator import InMemoryRunStore, ResearchRun
from resagent2_runtime import AgentAction
from resagent2_cli.main import _parser, _specs_for_existing_run


def _request(goal: str = "test goal") -> ResearchRequest:
    return ResearchRequest(goal=goal, budget=RunBudget(max_llm_calls=10, timeout_seconds=60), permissions=RunPermissions(execute_commands=True, prepare_environment=True), execution_limits=ExecutionLimits(max_tasks=2, max_attempts_per_task=1))


def _run(status: RunStatus = RunStatus.COMPLETED) -> ResearchRun:
    now = datetime.now(UTC)
    return ResearchRun(
        run_id="run_test",
        request=_request(),
        status=status,
        created_at=now,
        updated_at=now,
    )


class _Controller:
    def __init__(self, result: ResearchRun) -> None:
        self.result = result
        self.created = None
        self.answered = None
        self.resumed = None

    def create_run(self, run_id, request):
        self.created = (run_id, request)
        return self.result

    def answer_question(self, run_id, answer):
        self.answered = (run_id, answer)
        return self.result

    def run_until_stable(self, run_id):
        self.resumed = run_id
        return self.result


class _Builder:
    def __init__(self, controller: _Controller) -> None:
        self.controller = controller
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return CliApplication(controller=self.controller, run_store=InMemoryRunStore())


def test_composition_reads_explicit_model_and_component_context_limits(monkeypatch):
    monkeypatch.setenv("RESAGENT2_CONTEXT_WINDOW", "32000")
    monkeypatch.setenv("RESAGENT2_RESERVED_OUTPUT_TOKENS", "3000")
    monkeypatch.setenv("RESAGENT2_CONTEXT_SAFETY_MARGIN_TOKENS", "1000")
    monkeypatch.setenv("RESAGENT2_CODING_CONTEXT_TOKENS", "12000")

    profile = composition._model_profile()
    assert profile.context_window == 32000
    assert profile.reserved_output_tokens == 3000
    assert profile.safety_margin_tokens == 1000
    assert composition._component_context_limit("coding") == 12000


def test_agents_and_compiler_share_default_with_independent_overrides(monkeypatch):
    for component, default in (
        ("coding", 128_000), ("experiment", 128_000),
        ("scientific", 128_000), ("compiler", 128_000),
    ):
        name = f"RESAGENT2_{component.upper()}_CONTEXT_TOKENS"
        monkeypatch.delenv(name, raising=False)
        assert composition._component_context_limit(component) == default
        monkeypatch.setenv(name, "1024")
        assert composition._component_context_limit(component) == 1024


def test_compiler_reuses_context_composer_without_agent_loop(monkeypatch):
    class _Client:
        last_attempts = 1

        def context_budget(self, action_type, component_limit):
            return component_limit

        def next_action(self, context, action_type):
            self.context = context
            return {"tool": "finish"}

    runtime_client = _Client()
    monkeypatch.setattr(composition, "_client", lambda: runtime_client)
    compiler_client = composition._compiler_client(max_context_tokens=512)

    compiler_client.next_action("Compile this objective", AgentAction)

    assert runtime_client.context.included_sections == [
        "system",
        "compiler_request",
    ]
    assert runtime_client.context.estimated_tokens > 0


def test_run_passes_goal_and_workspace_to_existing_interfaces(tmp_path: Path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    controller = _Controller(_run())
    builder = _Builder(controller)

    result = cli(
        [
            "run",
            "--run-id",
            "run_test",
            "--goal",
            "inspect this repository",
            "--workspace",
            str(workspace),
            "--data-root",
            str(tmp_path / "data"),
        ],
        application_builder=builder,
    )

    assert result == EXIT_COMPLETED
    assert controller.created[0] == "run_test"
    assert controller.created[1].goal == "inspect this repository"
    assert builder.calls[0]["workspaces"]["ws_main"].location == str(workspace.resolve())


@pytest.mark.parametrize("names", [[], ["metrics.json", "Summary", "metrics.json"]])
def test_run_passes_only_explicit_required_artifacts_to_controller(tmp_path, names):
    controller = _Controller(_run())
    flags = [part for name in names for part in ("--required-artifact", name)]

    result = cli(
        ["run", "--goal", "Produce metrics.json", "--data-root", str(tmp_path), *flags],
        application_builder=_Builder(controller),
    )

    assert result == EXIT_COMPLETED
    assert controller.created[1].required_artifacts == names


def test_run_rejects_invalid_required_artifact_before_building_application(tmp_path):
    controller = _Controller(_run())
    builder = _Builder(controller)

    with pytest.raises(ValidationError, match="required_artifacts"):
        cli(
            ["run", "--goal", "Produce metrics", "--data-root", str(tmp_path),
             "--required-artifact", "results/metrics.json"],
            application_builder=builder,
        )

    assert controller.created is None
    assert builder.calls == []


def test_goal_file_is_read_explicitly(tmp_path: Path):
    goal_file = tmp_path / "goal.txt"
    goal_file.write_text("a long research goal", encoding="utf-8")
    controller = _Controller(_run())

    result = cli(
        [
            "run",
            "--run-id",
            "run_test",
            "--goal-file",
            str(goal_file),
            "--data-root",
            str(tmp_path / "data"),
        ],
        application_builder=_Builder(controller),
    )

    assert result == EXIT_COMPLETED
    assert controller.created[1].goal == "a long research goal"


@pytest.mark.parametrize("answer", ["accuracy", "第二个", "第二个，mul=2*3"])
def test_answer_uses_persisted_question(tmp_path: Path, answer: str):
    run = _run(RunStatus.PAUSED)
    run.pending_question = PendingQuestion(
        id="question_metric",
        run_id=run.run_id,
        text="Choose the primary metric",
        requested_fields=["primary_metric"],
        created_at=datetime.now(UTC),
    )
    store = InMemoryRunStore()
    store.save(run)
    controller = _Controller(_run())

    result = cli(
        [
            "answer",
            run.run_id,
            "--field",
            f"primary_metric={answer}",
            "--data-root",
            str(tmp_path / "data"),
        ],
        application_builder=_Builder(controller),
        store_factory=lambda root: store,
    )

    assert result == EXIT_COMPLETED
    assert controller.answered[0] == run.run_id
    assert controller.answered[1].question_id == "question_metric"
    assert controller.answered[1].values == {"primary_metric": answer}


def test_answer_rejects_invalid_key_before_calling_controller(tmp_path: Path):
    run = _run(RunStatus.PAUSED)
    run.pending_question = PendingQuestion(
        id="question_mode", run_id=run.run_id, text="Which mode?",
        requested_fields=["mode"], created_at=datetime.now(UTC),
    )
    store = InMemoryRunStore()
    store.save(run)
    controller = _Controller(_run())

    with pytest.raises(ValidationError, match="String should match pattern"):
        cli(
            ["answer", run.run_id, "--field", "mode choice=第二个",
             "--data-root", str(tmp_path / "data")],
            application_builder=_Builder(controller), store_factory=lambda root: store,
        )

    assert controller.answered is None
    assert store.load(run.run_id).pending_question == run.pending_question


def test_show_reads_store_without_building_application(tmp_path: Path, capsys):
    store = InMemoryRunStore()
    store.save(_run(RunStatus.PAUSED))

    result = cli(
        ["show", "run_test", "--data-root", str(tmp_path / "data")],
        application_builder=lambda **kwargs: (_ for _ in ()).throw(AssertionError()),
        store_factory=lambda root: store,
    )

    assert result == EXIT_COMPLETED
    assert "Status: paused" in capsys.readouterr().out


def test_resume_uses_controller_and_returns_paused_exit(tmp_path: Path):
    run = _run(RunStatus.RUNNING)
    store = InMemoryRunStore()
    store.save(run)
    controller = _Controller(_run(RunStatus.PAUSED))

    result = cli(
        ["resume", run.run_id, "--data-root", str(tmp_path / "data")],
        application_builder=_Builder(controller),
        store_factory=lambda root: store,
    )

    assert result == EXIT_PAUSED
    assert controller.resumed == run.run_id


def test_production_composition_builds_without_calling_external_services(tmp_path: Path):
    application = build_application(data_root=tmp_path)

    assert application.controller.scheduler.store is application.run_store


def test_production_composition_loads_the_shared_dataset_catalog(
    tmp_path: Path, monkeypatch
):
    dataset_root = tmp_path / "datasets"
    (dataset_root / "cifar-10").mkdir(parents=True)
    (dataset_root / "catalog.json").write_text(
        '{"cifar10": "cifar-10"}', encoding="utf-8"
    )
    monkeypatch.setenv("RESAGENT2_DATASET_ROOT", str(dataset_root))

    application = build_application(data_root=tmp_path / "data")

    assert application.controller.dataset_ref_source.references() == [
        DatasetRef(dataset_id="cifar10", relative_path="cifar-10")
    ]


def test_coding_and_experiment_share_resource_layout(tmp_path: Path):
    application = build_application(data_root=tmp_path)
    bindings = application.controller.scheduler.bindings

    coding = bindings[WorkflowAgentKind.CODING].port
    experiment = bindings[WorkflowAgentKind.EXPERIMENT].port

    # Both execution agents must resolve envs/resources from the same layout, so
    # a code_modify prepare_environment and a follow-on experiment_run see the
    # same physical env prefix.
    assert coding.resource_layout is experiment.resource_layout
    assert application.controller.scientific_port.resource_layout is coding.resource_layout


def test_resume_reuses_persisted_environment_when_python_flag_is_omitted(
    tmp_path: Path,
):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    source = WorkspaceSpec(workspace_id='ws_main', source_kind=WorkspaceSourceKind.LOCAL, location=str(workspace), environment=EnvironmentSpec(python_version='3.12'), access=WorkspaceAccess(read_paths=['.'], write_paths=['.']))
    run = _run(RunStatus.PAUSED)
    run.workspaces["ws_main"] = WorkspaceRecord(
        workspace_id="ws_main",
        root=str(workspace),
        source=source,
        managed=False,
    )
    args = _parser().parse_args(["resume", run.run_id, "--workspace", str(workspace)])

    assert _specs_for_existing_run(args, run) == {"ws_main": source}


@pytest.mark.parametrize("command", ["answer", "resume"])
def test_recovery_rejects_adding_workspace(tmp_path, command):
    run = _run(RunStatus.PAUSED)
    store = InMemoryRunStore()
    store.save(run)
    controller = _Controller(run)
    builder = _Builder(controller)
    tokens = [command, run.run_id, "--workspace", str(tmp_path)]
    if command == "answer":
        tokens += ["--field", "ready=yes"]
    with pytest.raises(ValueError, match="cannot add a workspace"):
        cli(tokens, application_builder=builder, store_factory=lambda root: store)
    assert builder.calls == []
    assert store.load(run.run_id).model_dump_json() == run.model_dump_json()


@pytest.mark.parametrize("flags", [["--read-only"], ["--read-path", "src"],
                                   ["--write-path", "src"], ["--deny-path", "secrets"]])
def test_workspace_permissions_require_a_workspace_source(flags):
    args = _parser().parse_args(["resume", "run_test"] + flags)
    with pytest.raises(ValueError, match="require --workspace or --git"):
        _specs_for_existing_run(args, _run())


@pytest.mark.parametrize("flags,accepted", [
    (["--read-path", "src", "--read-only", "--deny-path", "src/private"], True),
    (["--read-path", "src", "--read-only"], False),
    (["--read-path", "src", "--write-path", "src", "--deny-path", "src/private"], False),
    (["--read-only"], False),
    ([], False),
])
def test_recovery_workspace_flags_can_only_repeat_persisted_grants(tmp_path, flags, accepted):
    run = _run(RunStatus.PAUSED)
    source = WorkspaceSpec(
        workspace_id="ws_main", source_kind="local", location=str(tmp_path),
        access=WorkspaceAccess(read_paths=["src"], write_paths=[], denied_paths=["src/private"]),
    )
    run.workspaces["ws_main"] = WorkspaceRecord(
        workspace_id="ws_main", root=str(tmp_path), source=source, managed=False,
    )
    args = _parser().parse_args(["resume", run.run_id, "--workspace", str(tmp_path)] + flags)
    if accepted:
        assert _specs_for_existing_run(args, run) == {"ws_main": source}
    else:
        with pytest.raises(ValueError, match="does not match"):
            _specs_for_existing_run(args, run)
    assert _specs_for_existing_run(_parser().parse_args(["resume", run.run_id]), run) == {
        "ws_main": source,
    }
