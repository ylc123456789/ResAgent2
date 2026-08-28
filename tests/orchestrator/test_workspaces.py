"""Workspace resolution tests for the scheduler (DEVELOPMENT_PLAN §10.6)."""

import pytest

from resagent2_contracts import (
    AgentOwner,
    Capability,
    ExperimentRunInput,
    ResearchRequest,
    RunBudget,
    TaskProposal,
    WorkflowProposal,
    WorkspaceSourceKind,
    WorkspaceSpec,
)
from resagent2_orchestrator import (
    InMemoryRunStore,
    ModuleBinding,
    OrchestrationError,
    ScriptedModulePort,
    WorkflowScheduler,
)


def _request() -> ResearchRequest:
    return ResearchRequest(
        goal="Evaluate",
        budget=RunBudget(
            max_tasks=5, max_attempts_per_task=2, max_llm_calls=20, timeout_seconds=60
        ),
    )


def _proposal(workspace_id: str | None = None) -> WorkflowProposal:
    return WorkflowProposal(
        work_request_id="work_1",
        summary="one task",
        compilation_rationale="workspace resolution test",
        tasks=[
            TaskProposal(
                id="task_exp",
                work_request_id="work_1",
                capability=Capability.EXPERIMENT_RUN,
                goal="Run",
                rationale="evidence",
                workspace_id=workspace_id,
                inputs=ExperimentRunInput(instructions="Run"),
            )
        ],
    )


def _local_spec(location) -> WorkspaceSpec:
    return WorkspaceSpec(
        workspace_id="ws_a", source_kind=WorkspaceSourceKind.LOCAL, location=str(location)
    )


def _scheduler(workspaces, *, data_root=None) -> WorkflowScheduler:
    return WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT, port=ScriptedModulePort([])
            )
        },
        store=InMemoryRunStore(),
        data_root=data_root,
        workspaces=workspaces,
    )


def test_single_workspace_is_auto_filled(tmp_path) -> None:
    engine = _scheduler({"ws_a": _local_spec(tmp_path / "repo")})

    run = engine.create_run("run_x", _request(), _proposal(workspace_id=None))

    assert run.workflow.tasks[0].workspace_id == "ws_a"
    assert "ws_a" in run.workspaces


def test_declared_workspace_id_is_preserved(tmp_path) -> None:
    engine = _scheduler({"ws_a": _local_spec(tmp_path / "repo")})

    run = engine.create_run("run_x", _request(), _proposal(workspace_id="ws_a"))

    assert run.workflow.tasks[0].workspace_id == "ws_a"


def test_undeclared_workspace_id_is_rejected(tmp_path) -> None:
    engine = _scheduler({"ws_a": _local_spec(tmp_path / "repo")})

    with pytest.raises(OrchestrationError, match="unknown workspace_id"):
        engine.create_run("run_x", _request(), _proposal(workspace_id="ws_evil"))


def test_multiple_workspaces_require_explicit_id(tmp_path) -> None:
    engine = _scheduler(
        {
            "ws_a": _local_spec(tmp_path / "repo_a"),
            "ws_b": _local_spec(tmp_path / "repo_b"),
        }
    )

    with pytest.raises(OrchestrationError, match="must declare a workspace_id"):
        engine.create_run("run_x", _request(), _proposal(workspace_id=None))


def test_two_workspaces_resolve_to_distinct_roots(tmp_path) -> None:
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    engine = _scheduler(
        {
            "ws_a": WorkspaceSpec(
                workspace_id="ws_a",
                source_kind=WorkspaceSourceKind.LOCAL,
                location=str(repo_a),
            ),
            "ws_b": WorkspaceSpec(
                workspace_id="ws_b",
                source_kind=WorkspaceSourceKind.LOCAL,
                location=str(repo_b),
            ),
        }
    )

    run = engine.create_run("run_x", _request(), _proposal(workspace_id="ws_a"))

    assert run.workspaces["ws_a"].root == str(repo_a.resolve())
    assert run.workspaces["ws_b"].root == str(repo_b.resolve())
    assert run.workspaces["ws_a"].managed is False
    assert run.workspaces["ws_b"].managed is False


def test_managed_workspace_defaults_to_data_root_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESAGENT2_DATA_ROOT", str(tmp_path / "data"))
    engine = _scheduler(
        {"ws_a": WorkspaceSpec(workspace_id="ws_a", source_kind=WorkspaceSourceKind.GENERATED)}
    )

    run = engine.create_run("run_x", _request(), _proposal(workspace_id="ws_a"))

    assert run.workspaces["ws_a"].managed is True
    assert run.workspaces["ws_a"].root == str(
        tmp_path / "data" / "runs" / "run_x" / "workspaces" / "ws_a" / "repo"
    )


def test_managed_workspace_root_is_under_data_root(tmp_path) -> None:
    engine = _scheduler(
        {"ws_a": WorkspaceSpec(workspace_id="ws_a", source_kind=WorkspaceSourceKind.GENERATED)},
        data_root=tmp_path / "data",
    )

    run = engine.create_run("run_x", _request(), _proposal(workspace_id="ws_a"))

    expected = (
        tmp_path / "data" / "runs" / "run_x" / "workspaces" / "ws_a" / "repo"
    )
    assert run.workspaces["ws_a"].root == str(expected)
    assert run.workspaces["ws_a"].managed is True
