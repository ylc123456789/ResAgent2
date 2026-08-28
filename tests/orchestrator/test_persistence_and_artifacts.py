from pathlib import Path

import pytest

from resagent2_contracts import (
    AgentOwner,
    ArtifactCandidate,
    Capability,
    ErrorCode,
    ExperimentRunInput,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    ResearchRequest,
    RunBudget,
    TaskProposal,
    WorkflowPatch,
    WorkflowProposal,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSource,
    ScientificAnalyzeInput,
)
from resagent2_orchestrator import (
    JsonRunStore,
    ModuleBinding,
    OrchestrationError,
    ScriptedModulePort,
    WorkflowScheduler,
)


def request() -> ResearchRequest:
    return ResearchRequest(
        goal="Persist a workflow",
        budget=RunBudget(
            max_tasks=5,
            max_attempts_per_task=2,
            max_llm_calls=10,
            timeout_seconds=600,
        ),
    )


def proposal() -> WorkflowProposal:
    return WorkflowProposal(
        work_request_id="work_legacy_initial",
        summary="one task",
        compilation_rationale="Persistence test",
        tasks=[
            TaskProposal(
                id="task_experiment",
                work_request_id="work_legacy_initial",
                capability=Capability.EXPERIMENT_RUN,
                goal="Run a tiny experiment",
                rationale="Produce one evidence file",
                inputs=ExperimentRunInput(instructions="Run once"),
            )
        ],
    )


def test_artifact_is_hashed_copied_and_bound_to_attempt(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    result = ModuleResult(
        status=ModuleStatus.COMPLETED,
        summary="experiment completed",
        artifacts=[
            ArtifactCandidate(
                kind="experiment_result",
                path="metrics.json",
                media_type="application/json",
                summary="Evaluation metrics",
            )
        ],
    )
    store = JsonRunStore(tmp_path / "state")
    engine = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([result]),
                workspace=WorkspaceGrant(
                    root=str(workspace),
                    mode=WorkspaceMode.READ_WRITE,
                    allowed_paths=["."],
                    source=WorkspaceSource.EXISTING,
                ),
            )
        },
        store=store,
        artifact_root=tmp_path / "artifacts",
    )
    engine.create_run("run_artifact", request(), proposal())

    run = engine.run_until_stable("run_artifact")
    artifact = next(iter(run.artifacts.values()))

    assert artifact.task_id == "task_experiment"
    assert artifact.attempt_number == 1
    assert len(artifact.sha256) == 64
    assert Path(artifact.uri.removeprefix("file://")).is_file()
    assert run.workflow.tasks[0].attempts[0].artifact_ids == [artifact.id]


def test_dependency_artifacts_are_forwarded_to_downstream_request(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace-forward"
    workspace.mkdir()
    (workspace / "metrics.json").write_text("{}", encoding="utf-8")
    experiment_port = ScriptedModulePort(
        [
            ModuleResult(
                status=ModuleStatus.COMPLETED,
                summary="evidence",
                artifacts=[
                    ArtifactCandidate(
                        kind="experiment_result",
                        path="metrics.json",
                        media_type="application/json",
                        summary="metrics",
                    )
                ],
            )
        ]
    )
    analyze_port = ScriptedModulePort(
        [ModuleResult(status=ModuleStatus.COMPLETED, summary="analyzed")]
    )
    experiment = proposal().tasks[0]
    analyze = TaskProposal(
        id="task_analyze",
        work_request_id="work_legacy_initial",
        capability=Capability.SCIENTIFIC_ANALYZE,
        goal="Analyze evidence",
        rationale="Close the evidence loop",
        depends_on=["task_experiment"],
        inputs=ScientificAnalyzeInput(
            question="What happened?", evidence_artifact_ids=[]
        ),
    )
    engine = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=experiment_port,
                workspace=WorkspaceGrant(
                    root=str(workspace),
                    mode=WorkspaceMode.READ_WRITE,
                    allowed_paths=["."],
                    source=WorkspaceSource.EXISTING,
                ),
            ),
            Capability.SCIENTIFIC_ANALYZE: ModuleBinding(
                owner=AgentOwner.SCIENTIFIC,
                port=analyze_port,
            ),
        },
        store=JsonRunStore(tmp_path / "forward-state"),
        artifact_root=tmp_path / "forward-artifacts",
    )
    combined = WorkflowProposal(
        work_request_id="work_legacy_initial",
        summary="forward",
        compilation_rationale="Evidence must cross the module boundary",
        tasks=[experiment, analyze],
    )
    engine.create_run("run_forward", request(), combined)
    engine.run_until_stable("run_forward")

    assert len(analyze_port.requests[0].input_artifacts) == 1
    assert analyze_port.requests[0].input_artifacts[0].task_id == "task_experiment"


def test_failed_attempt_artifacts_are_not_forwarded_downstream(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace-retry"
    workspace.mkdir()
    (workspace / "crash.log").write_text("boom", encoding="utf-8")
    (workspace / "metrics.json").write_text("{}", encoding="utf-8")
    experiment_port = ScriptedModulePort(
        [
            ModuleResult(
                status=ModuleStatus.FAILED,
                summary="first attempt crashed",
                error=ModuleError(
                    code=ErrorCode.TOOL_FAILED,
                    message="experiment crashed",
                    retryable=True,
                ),
                artifacts=[
                    ArtifactCandidate(
                        kind="experiment_result",
                        path="crash.log",
                        media_type="text/plain",
                        summary="diagnostic log of the failed attempt",
                    )
                ],
            ),
            ModuleResult(
                status=ModuleStatus.COMPLETED,
                summary="evidence",
                artifacts=[
                    ArtifactCandidate(
                        kind="experiment_result",
                        path="metrics.json",
                        media_type="application/json",
                        summary="metrics",
                    )
                ],
            ),
        ]
    )
    analyze_port = ScriptedModulePort(
        [ModuleResult(status=ModuleStatus.COMPLETED, summary="analyzed")]
    )
    experiment = proposal().tasks[0]
    analyze = TaskProposal(
        id="task_analyze",
        work_request_id="work_legacy_initial",
        capability=Capability.SCIENTIFIC_ANALYZE,
        goal="Analyze evidence",
        rationale="Only verified evidence may cross the boundary",
        depends_on=["task_experiment"],
        inputs=ScientificAnalyzeInput(
            question="What happened?", evidence_artifact_ids=[]
        ),
    )
    engine = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=experiment_port,
                workspace=WorkspaceGrant(
                    root=str(workspace),
                    mode=WorkspaceMode.READ_WRITE,
                    allowed_paths=["."],
                    source=WorkspaceSource.EXISTING,
                ),
            ),
            Capability.SCIENTIFIC_ANALYZE: ModuleBinding(
                owner=AgentOwner.SCIENTIFIC,
                port=analyze_port,
            ),
        },
        store=JsonRunStore(tmp_path / "retry-state"),
        artifact_root=tmp_path / "retry-artifacts",
    )
    combined = WorkflowProposal(
        work_request_id="work_legacy_initial",
        summary="retry",
        compilation_rationale="A retried task must not leak its failed evidence",
        tasks=[experiment, analyze],
    )
    engine.create_run("run_retry", request(), combined)
    run = engine.run_until_stable("run_retry")

    assert len(run.artifacts) == 2
    assert len(analyze_port.requests[0].input_artifacts) == 1
    assert analyze_port.requests[0].input_artifacts[0].attempt_number == 2


def test_json_store_recovers_after_scheduler_restart(tmp_path: Path) -> None:
    store = JsonRunStore(tmp_path / "state")
    first_port = ScriptedModulePort(
        [ModuleResult(status=ModuleStatus.COMPLETED, summary="done")]
    )
    binding = ModuleBinding(owner=AgentOwner.EXPERIMENT, port=first_port)
    first = WorkflowScheduler(
        bindings={Capability.EXPERIMENT_RUN: binding},
        store=store,
        artifact_root=tmp_path / "artifacts",
    )
    first.create_run("run_restart", request(), proposal())

    second = WorkflowScheduler(
        bindings={Capability.EXPERIMENT_RUN: binding},
        store=JsonRunStore(tmp_path / "state"),
        artifact_root=tmp_path / "artifacts",
    )
    recovered = second.run_until_stable("run_restart")

    assert recovered.status.value == "completed"
    assert recovered.workflow.tasks[0].attempts[0].number == 1


def test_stale_patch_and_missing_capability_binding_are_rejected(tmp_path: Path) -> None:
    engine = WorkflowScheduler(
        bindings={},
        store=JsonRunStore(tmp_path / "state"),
        artifact_root=tmp_path / "artifacts",
    )
    with pytest.raises(OrchestrationError, match="no ModulePort"):
        engine.create_run("run_invalid", request(), proposal())

    valid = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([]),
            )
        },
        store=JsonRunStore(tmp_path / "valid-state"),
        artifact_root=tmp_path / "artifacts",
    )
    valid.create_run("run_patch", request(), proposal())
    with pytest.raises(OrchestrationError, match="revision"):
        valid.apply_patch(
            "run_patch",
            WorkflowPatch(
                work_request_id="work_legacy_initial",
                based_on_revision=2,
                reason="stale",
            ),
        )


def test_unknown_capability_is_rejected_by_the_contract() -> None:
    raw = proposal().model_dump(mode="json")
    raw["tasks"][0]["capability"] = "unknown_capability"
    raw["tasks"][0]["inputs"]["capability"] = "unknown_capability"

    with pytest.raises(ValueError, match="unknown_capability"):
        WorkflowProposal.model_validate(raw)
