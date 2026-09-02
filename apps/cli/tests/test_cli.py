from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from resagent2_cli import composition
from resagent2_cli.composition import CliApplication, build_application
from resagent2_cli.main import EXIT_COMPLETED, EXIT_PAUSED, cli
from resagent2_contracts import (
    Capability,
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
from resagent2_cli.main import _specs_for_existing_run


def _request(goal: str = "test goal") -> ResearchRequest:
    return ResearchRequest(
        goal=goal,
        budget=RunBudget(
            max_tasks=2,
            max_attempts_per_task=1,
            max_llm_calls=10,
            timeout_seconds=60,
        ),
    )


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
    compiler_client = composition._CompilerClient(max_context_tokens=512)

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


def test_answer_uses_persisted_question(tmp_path: Path):
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
            "primary_metric=accuracy",
            "--data-root",
            str(tmp_path / "data"),
        ],
        application_builder=_Builder(controller),
        store_factory=lambda root: store,
    )

    assert result == EXIT_COMPLETED
    assert controller.answered[0] == run.run_id
    assert controller.answered[1].question_id == "question_metric"
    assert controller.answered[1].values == {"primary_metric": "accuracy"}


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


def test_coding_and_experiment_share_resource_layout(tmp_path: Path):
    application = build_application(data_root=tmp_path)
    bindings = application.controller.scheduler.bindings

    coding = bindings[Capability.CODE_MODIFY].port
    experiment = bindings[Capability.EXPERIMENT_RUN].port

    # Both execution agents must resolve envs/resources from the same layout, so
    # a code_modify prepare_environment and a follow-on experiment_run see the
    # same physical env prefix.
    assert coding.resource_layout is experiment.resource_layout


def test_resume_reuses_persisted_environment_when_python_flag_is_omitted(
    tmp_path: Path,
):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    source = WorkspaceSpec(
        workspace_id="ws_main",
        source_kind=WorkspaceSourceKind.LOCAL,
        location=str(workspace),
        environment=EnvironmentSpec(python_version="3.12"),
    )
    run = _run(RunStatus.PAUSED)
    run.workspaces["ws_main"] = WorkspaceRecord(
        workspace_id="ws_main",
        root=str(workspace),
        source=source,
        managed=False,
    )
    args = type(
        "Args",
        (),
        {"workspace": str(workspace), "git": None, "python_version": None},
    )()

    assert _specs_for_existing_run(args, run) == {"ws_main": source}
