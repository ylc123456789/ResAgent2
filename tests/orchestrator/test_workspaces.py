"""Workspace resolution tests for the scheduler (DEVELOPMENT_PLAN §10.6)."""

from resagent2_contracts import WorkspaceAccess, RunPermissions, ExecutionLimits

from datetime import UTC, datetime

import pytest

from resagent2_contracts import (
    AgentOwner,
    AgentResult,
    ModuleStatus,
    ResearchRequest,
    RunBudget,
    RunStatus,
    TaskProposal,
    TaskStatus,
    WorkflowAgentKind,
    WorkflowProposal,
    WorkspaceSourceKind,
    WorkspaceSpec,
)
from resagent2_orchestrator import (
    InMemoryRunStore,
    ModuleBinding,
    OrchestrationError,
    ResearchRun,
    ScriptedModulePort,
    WorkflowScheduler,
)


def _request() -> ResearchRequest:
    return ResearchRequest(goal='Evaluate', budget=RunBudget(max_llm_calls=20, timeout_seconds=60), permissions=RunPermissions(execute_commands=True, prepare_environment=True), execution_limits=ExecutionLimits(max_tasks=5, max_attempts_per_task=2))


def _proposal(workspace_id: str | None = None) -> WorkflowProposal:
    return WorkflowProposal(
        work_request_id="work_1",
        tasks=[
            TaskProposal(
                id="task_exp",
                work_request_id="work_1",
                workflow_agent_kind=WorkflowAgentKind.EXPERIMENT,
                instruction="Run",
                workspace_id=workspace_id,

            )
        ],
    )


def _local_spec(location) -> WorkspaceSpec:
    return WorkspaceSpec(workspace_id='ws_a', source_kind=WorkspaceSourceKind.LOCAL, location=str(location), access=WorkspaceAccess(read_paths=['.'], write_paths=['.']))


def _scheduler(workspaces, *, data_root=None) -> WorkflowScheduler:
    return WorkflowScheduler(
        bindings={
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT, port=ScriptedModulePort([])
            )
        },
        store=InMemoryRunStore(),
        data_root=data_root,
        workspaces=workspaces,
    )


def _create_run(engine, run_id, request, proposal):
    """Construct a bare run then attach a workflow (ADR-0011: no scheduler create_run)."""
    now = datetime.now(UTC)
    engine.store.save(
        ResearchRun(
            run_id=run_id,
            request=request,
            workspaces=engine._resolve_workspaces(run_id),
            status=RunStatus.RUNNING,
            created_at=now,
            updated_at=now,
        )
    )
    return engine.accept_proposal(run_id, proposal)


def test_single_workspace_is_auto_filled(tmp_path) -> None:
    engine = _scheduler({"ws_a": _local_spec(tmp_path / "repo")})

    run = _create_run(engine, "run_x", _request(), _proposal(workspace_id=None))

    assert run.workflow.tasks[0].workspace_id == "ws_a"
    assert "ws_a" in run.workspaces


def test_declared_workspace_id_is_preserved(tmp_path) -> None:
    engine = _scheduler({"ws_a": _local_spec(tmp_path / "repo")})

    run = _create_run(engine, "run_x", _request(), _proposal(workspace_id="ws_a"))

    assert run.workflow.tasks[0].workspace_id == "ws_a"


def test_undeclared_workspace_id_is_rejected(tmp_path) -> None:
    engine = _scheduler({"ws_a": _local_spec(tmp_path / "repo")})

    with pytest.raises(OrchestrationError, match="unknown workspace_id"):
        _create_run(engine, "run_x", _request(), _proposal(workspace_id="ws_evil"))


def test_multiple_workspaces_require_explicit_id(tmp_path) -> None:
    engine = _scheduler({'ws_a': WorkspaceSpec(workspace_id='ws_a', source_kind=WorkspaceSourceKind.LOCAL, location=str(tmp_path / 'repo_a'), access=WorkspaceAccess(read_paths=['.'], write_paths=['.'])), 'ws_b': WorkspaceSpec(workspace_id='ws_b', source_kind=WorkspaceSourceKind.LOCAL, location=str(tmp_path / 'repo_b'), access=WorkspaceAccess(read_paths=['.'], write_paths=['.']))})

    with pytest.raises(OrchestrationError, match="workspace_id"):
        _create_run(engine, "run_x", _request(), _proposal(workspace_id=None))


def test_two_workspaces_resolve_to_distinct_roots(tmp_path) -> None:
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    engine = _scheduler({'ws_a': WorkspaceSpec(workspace_id='ws_a', source_kind=WorkspaceSourceKind.LOCAL, location=str(repo_a), access=WorkspaceAccess(read_paths=['.'], write_paths=['.'])), 'ws_b': WorkspaceSpec(workspace_id='ws_b', source_kind=WorkspaceSourceKind.LOCAL, location=str(repo_b), access=WorkspaceAccess(read_paths=['.'], write_paths=['.']))})

    run = _create_run(engine, "run_x", _request(), _proposal(workspace_id="ws_a"))

    assert run.workspaces["ws_a"].root == str(repo_a.resolve())
    assert run.workspaces["ws_b"].root == str(repo_b.resolve())
    assert run.workspaces["ws_a"].managed is False
    assert run.workspaces["ws_b"].managed is False


def test_same_workspace_id_gives_same_root_to_coding_and_experiment(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    coding_port = ScriptedModulePort(
        [AgentResult(status=ModuleStatus.COMPLETED, report="code")]
    )
    experiment_port = ScriptedModulePort(
        [AgentResult(status=ModuleStatus.COMPLETED, report="exp")]
    )
    engine = WorkflowScheduler(bindings={WorkflowAgentKind.CODING: ModuleBinding(owner=AgentOwner.CODING, port=coding_port), WorkflowAgentKind.EXPERIMENT: ModuleBinding(owner=AgentOwner.EXPERIMENT, port=experiment_port)}, store=InMemoryRunStore(), data_root=tmp_path / 'data', workspaces={'ws_main': WorkspaceSpec(workspace_id='ws_main', source_kind=WorkspaceSourceKind.LOCAL, location=str(repo), access=WorkspaceAccess(read_paths=['.'], write_paths=['.']))})
    proposal = WorkflowProposal(
        work_request_id="work_1",
        tasks=[
            TaskProposal(
                id="task_code",
                work_request_id="work_1",
                workflow_agent_kind=WorkflowAgentKind.CODING,
                instruction="Code",
                workspace_id="ws_main",

            ),
            TaskProposal(
                id="task_exp",
                work_request_id="work_1",
                workflow_agent_kind=WorkflowAgentKind.EXPERIMENT,
                instruction="Exp",
                workspace_id="ws_main",
                depends_on=["task_code"],

            ),
        ],
    )

    _create_run(engine, "run_x", _request(), proposal)
    run = engine.run_until_stable("run_x")

    assert [task.status for task in run.workflow.tasks] == [
        TaskStatus.COMPLETED,
        TaskStatus.COMPLETED,
    ]
    assert coding_port.requests[0].workspace.root == str(repo.resolve())
    assert experiment_port.requests[0].workspace.root == str(repo.resolve())


def test_managed_workspace_defaults_to_data_root_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESAGENT2_DATA_ROOT", str(tmp_path / "data"))
    engine = _scheduler({'ws_a': WorkspaceSpec(workspace_id='ws_a', source_kind=WorkspaceSourceKind.GENERATED, access=WorkspaceAccess(read_paths=['.'], write_paths=['.']))})

    run = _create_run(engine, "run_x", _request(), _proposal(workspace_id="ws_a"))

    assert run.workspaces["ws_a"].managed is True
    assert run.workspaces["ws_a"].root == str(
        tmp_path / "data" / "runs" / "run_x" / "workspaces" / "ws_a" / "repo"
    )


def test_managed_workspace_root_is_under_data_root(tmp_path) -> None:
    engine = _scheduler({'ws_a': WorkspaceSpec(workspace_id='ws_a', source_kind=WorkspaceSourceKind.GENERATED, access=WorkspaceAccess(read_paths=['.'], write_paths=['.']))}, data_root=tmp_path / 'data')

    run = _create_run(engine, "run_x", _request(), _proposal(workspace_id="ws_a"))

    expected = (
        tmp_path / "data" / "runs" / "run_x" / "workspaces" / "ws_a" / "repo"
    )
    assert run.workspaces["ws_a"].root == str(expected)
    assert run.workspaces["ws_a"].managed is True


def test_workspace_environment_and_run_datasets_reach_module_request(tmp_path) -> None:
    from resagent2_contracts import (DatasetRef, EnvironmentSpec, ArtifactCandidate, ControlSignal)

    port = ScriptedModulePort(
        [AgentResult(status=ModuleStatus.COMPLETED, report="ok")]
    )
    engine = WorkflowScheduler(bindings={WorkflowAgentKind.EXPERIMENT: ModuleBinding(owner=AgentOwner.EXPERIMENT, port=port)}, store=InMemoryRunStore(), workspaces={'ws_main': WorkspaceSpec(workspace_id='ws_main', source_kind=WorkspaceSourceKind.LOCAL, location=str(tmp_path), environment=EnvironmentSpec(python_version='3.10'), access=WorkspaceAccess(read_paths=['.'], write_paths=['.']))})
    request = ResearchRequest(goal='Run', budget=RunBudget(max_llm_calls=20, timeout_seconds=60), permissions=RunPermissions(execute_commands=True, prepare_environment=True), execution_limits=ExecutionLimits(max_tasks=5, max_attempts_per_task=2))
    proposal = WorkflowProposal(
        work_request_id="work_1",
        tasks=[
            TaskProposal(
                id="task_exp",
                work_request_id="work_1",
                workflow_agent_kind=WorkflowAgentKind.EXPERIMENT,
                instruction="Run",
                workspace_id="ws_main",

            )
        ],
    )
    _create_run(engine, "run_env", request, proposal)
    run = engine.store.load("run_env")
    run.dataset_refs = [DatasetRef(dataset_id="cifar10", relative_path="cifar10")]
    from resagent2_orchestrator.handoffs import read_json, system_artifact
    run.dataset_catalog_ref = system_artifact(engine.artifact_registry, run, "dataset_catalog", {
        "datasets": [ref.model_dump(mode="json") for ref in run.dataset_refs],
    })
    engine.store.save(run)
    engine.run_until_stable("run_env")

    req = port.requests[0]
    assert req.environment_spec.python_version == "3.10"
    catalog = next(ref for ref in req.input_artifacts if ref.kind == "dataset_catalog")
    assert read_json(catalog)["datasets"][0]["dataset_id"] == "cifar10"
