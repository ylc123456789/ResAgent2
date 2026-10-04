
from resagent2_contracts import RunPermissions, ExecutionLimits
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from resagent2_contracts import (
    AgentOwner,
    AgentResult,
    ArtifactId,
    ArtifactCandidate,
    ArtifactImport,
    ErrorCode,
    ModuleError,
    ModuleStatus,
    ResearchRequest,
    RunBudget,
    RunStatus,
    TaskId,
    TaskProposal,
    WorkflowAgentKind,
    WorkflowPatch,
    WorkflowProposal,
    WorkspaceGrant,
    WorkspaceAccess,
    WorkspaceSourceKind,
    WorkspaceSpec,
)
from resagent2_orchestrator import (
    ArtifactRegistrationError,
    ArtifactRegistry,
    JsonRunStore,
    ModuleBinding,
    OrchestrationError,
    ResearchRun,
    ScriptedModulePort,
    WorkflowScheduler,
)


def _create_run(engine, run_id, request, proposal):
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


def request() -> ResearchRequest:
    return ResearchRequest(goal='Persist a workflow', budget=RunBudget(max_llm_calls=10, timeout_seconds=600), permissions=RunPermissions(execute_commands=True, prepare_environment=True), execution_limits=ExecutionLimits(max_tasks=5, max_attempts_per_task=2))


def proposal() -> WorkflowProposal:
    return WorkflowProposal(
        work_request_id="work_legacy_initial",
        tasks=[
            TaskProposal(
                id="task_experiment",
                work_request_id="work_legacy_initial",
                workflow_agent_kind=WorkflowAgentKind.EXPERIMENT,
                instruction="Run a tiny experiment",

            )
        ],
    )


@pytest.mark.parametrize("schema_version", ["8.0", "9.0", "19.0"])
def test_old_schema_run_is_rejected_without_rewriting_its_file(tmp_path: Path, schema_version) -> None:
    now = datetime.now(UTC)
    run = ResearchRun(
        run_id="run_old_schema", request=request(), status=RunStatus.PENDING,
        created_at=now, updated_at=now,
    )
    data = run.model_dump(mode="json")
    data["request"]["schema_version"] = schema_version
    data["request"]["budget"]["schema_version"] = schema_version
    store = JsonRunStore(tmp_path / "old-state")
    path = store.root / "run_old_schema.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(ValidationError, match="schema_version"):
        store.load(run.run_id)

    assert path.read_bytes() == before


def test_artifact_is_hashed_copied_and_bound_to_attempt(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    result = AgentResult(
        status=ModuleStatus.COMPLETED,
        report="experiment completed",

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
    engine = WorkflowScheduler(bindings={WorkflowAgentKind.EXPERIMENT: ModuleBinding(owner=AgentOwner.EXPERIMENT, port=ScriptedModulePort([result]))}, store=store, artifact_root=tmp_path / 'artifacts', workspaces={'ws_main': WorkspaceSpec(workspace_id='ws_main', source_kind=WorkspaceSourceKind.LOCAL, location=str(workspace), access=WorkspaceAccess(read_paths=['.'], write_paths=['.']))})
    _create_run(engine, "run_artifact", request(), proposal())

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
            AgentResult(
                status=ModuleStatus.COMPLETED,
                report="evidence",

                artifacts=[
                    ArtifactCandidate(
                        kind="experiment_result",
                        path="metrics.json",
                        media_type="application/json",
                        summary="metrics", output_name="metrics",
                    )
                ],
            )
        ]
    )
    analyze_port = ScriptedModulePort(
        [AgentResult(status=ModuleStatus.COMPLETED, report="analyzed",
                      )]
    )
    experiment = proposal().tasks[0]
    experiment.output_names = ["metrics"]
    analyze = TaskProposal(
        id="task_analyze",
        work_request_id="work_legacy_initial",
        workflow_agent_kind=WorkflowAgentKind.CODING,
        instruction="Analyze evidence",
        depends_on=["task_experiment"],
        input_artifact_bindings=[dict(source_task="task_experiment", output_selector="metrics")],

    )
    engine = WorkflowScheduler(bindings={WorkflowAgentKind.EXPERIMENT: ModuleBinding(owner=AgentOwner.EXPERIMENT, port=experiment_port), WorkflowAgentKind.CODING: ModuleBinding(owner=AgentOwner.CODING, port=analyze_port)}, store=JsonRunStore(tmp_path / 'forward-state'), artifact_root=tmp_path / 'forward-artifacts', workspaces={'ws_main': WorkspaceSpec(workspace_id='ws_main', source_kind=WorkspaceSourceKind.LOCAL, location=str(workspace), access=WorkspaceAccess(read_paths=['.'], write_paths=['.']))})
    combined = WorkflowProposal(
        work_request_id="work_legacy_initial",
        tasks=[experiment, analyze],
    )
    _create_run(engine, "run_forward", request(), combined)
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
            AgentResult(
                status=ModuleStatus.FAILED,
                report="first attempt crashed",
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
            AgentResult(
                status=ModuleStatus.COMPLETED,
                report="evidence",

                artifacts=[
                    ArtifactCandidate(
                        kind="experiment_result",
                        path="metrics.json",
                        media_type="application/json",
                        summary="metrics", output_name="metrics",
                    )
                ],
            ),
        ]
    )
    analyze_port = ScriptedModulePort(
        [AgentResult(status=ModuleStatus.COMPLETED, report="analyzed",
                      )]
    )
    experiment = proposal().tasks[0]
    experiment.output_names = ["metrics"]
    analyze = TaskProposal(
        id="task_analyze",
        work_request_id="work_legacy_initial",
        workflow_agent_kind=WorkflowAgentKind.CODING,
        instruction="Analyze evidence",
        depends_on=["task_experiment"],
        input_artifact_bindings=[dict(source_task="task_experiment", output_selector="metrics")],

    )
    engine = WorkflowScheduler(bindings={WorkflowAgentKind.EXPERIMENT: ModuleBinding(owner=AgentOwner.EXPERIMENT, port=experiment_port), WorkflowAgentKind.CODING: ModuleBinding(owner=AgentOwner.CODING, port=analyze_port)}, store=JsonRunStore(tmp_path / 'retry-state'), artifact_root=tmp_path / 'retry-artifacts', workspaces={'ws_main': WorkspaceSpec(workspace_id='ws_main', source_kind=WorkspaceSourceKind.LOCAL, location=str(workspace), access=WorkspaceAccess(read_paths=['.'], write_paths=['.']))})
    combined = WorkflowProposal(
        work_request_id="work_legacy_initial",
        tasks=[experiment, analyze],
    )
    _create_run(engine, "run_retry", request(), combined)
    run = engine.run_until_stable("run_retry")

    assert len([ref for ref in run.artifacts.values() if ref.kind != "acceptance_requirements"]) == 2
    assert len(analyze_port.requests[0].input_artifacts) == 1
    assert analyze_port.requests[0].input_artifacts[0].attempt_number == 2


def test_json_store_recovers_after_scheduler_restart(tmp_path: Path) -> None:
    store = JsonRunStore(tmp_path / "state")
    first_port = ScriptedModulePort(
        [AgentResult(status=ModuleStatus.COMPLETED, report="done")]
    )
    binding = ModuleBinding(owner=AgentOwner.EXPERIMENT, port=first_port)
    first = WorkflowScheduler(
        bindings={WorkflowAgentKind.EXPERIMENT: binding},
        store=store,
        artifact_root=tmp_path / "artifacts",
    )
    _create_run(first, "run_restart", request(), proposal())

    second = WorkflowScheduler(
        bindings={WorkflowAgentKind.EXPERIMENT: binding},
        store=JsonRunStore(tmp_path / "state"),
        artifact_root=tmp_path / "artifacts",
    )
    recovered = second.run_until_stable("run_restart")

    assert recovered.workflow.tasks[0].status.value == "completed"
    assert recovered.workflow.tasks[0].attempts[0].number == 1


def test_stale_patch_and_missing_agent_binding_are_rejected(tmp_path: Path) -> None:
    engine = WorkflowScheduler(
        bindings={},
        store=JsonRunStore(tmp_path / "state"),
        artifact_root=tmp_path / "artifacts",
    )
    with pytest.raises(OrchestrationError, match="no matching Agent binding"):
        _create_run(engine, "run_invalid", request(), proposal())

    valid = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([]),
            )
        },
        store=JsonRunStore(tmp_path / "valid-state"),
        artifact_root=tmp_path / "artifacts",
    )
    _create_run(valid, "run_patch", request(), proposal())
    with pytest.raises(OrchestrationError, match="revision"):
        valid.apply_patch(
            "run_patch",
            WorkflowPatch(
                work_request_id="work_legacy_initial",
                based_on_revision=2,
            ),
        )


def test_unknown_agent_kind_is_rejected_by_the_contract() -> None:
    raw = proposal().model_dump(mode="json")
    raw["tasks"][0]["workflow_agent_kind"] = "scientific"

    with pytest.raises(ValueError, match="scientific"):
        WorkflowProposal.model_validate(raw)


def test_register_import_freezes_copies_and_hashes(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4 evidence")
    registry = ArtifactRegistry(tmp_path / "artifacts")

    artifact = registry.register_import(
        ArtifactImport(
            uri=str(source),
            kind="paper",
            media_type="application/pdf",
            summary="A paper",
            expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        ),
        run_id="run_import",
    )

    assert artifact.producer == AgentOwner.ORCHESTRATOR
    assert artifact.metadata == {"source_type": "import"}
    assert artifact.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert Path(artifact.uri.removeprefix("file://")).is_file()


def test_register_import_rejects_hash_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"evidence")
    registry = ArtifactRegistry(tmp_path / "artifacts")

    with pytest.raises(ArtifactRegistrationError, match="mismatch"):
        registry.register_import(
            ArtifactImport(
                uri=str(source),
                kind="paper",
                media_type="application/pdf",
                summary="A paper",
                expected_sha256="a" * 64,
            ),
            run_id="run_import",
        )


def test_rejected_candidate_leaves_no_residue_directory(tmp_path: Path) -> None:
    """A candidate rejected before validation leaves no artifact directory, so a
    retry with the same id is not poisoned by a residue from the first attempt."""
    registry = ArtifactRegistry(tmp_path / "artifacts")
    candidate = ArtifactCandidate(
        kind="experiment_result",
        path="metrics.json",
        media_type="application/json",
        summary="no grant",
    )
    with pytest.raises(ArtifactRegistrationError, match="grant"):
        registry.register(
            candidate,
            grant=None,
            producer=AgentOwner.EXPERIMENT,
            run_id="run_x",
            task_id="task_x",
            attempt_number=1,
            index=1,
            existing_ids=set(),
        )
    assert not (tmp_path / "artifacts" / "run_x" / "artifact_x_1_1").exists()


def test_register_reuses_complete_artifact_after_crash(tmp_path: Path) -> None:
    """A crash after the staging dir was promoted but before the run index was
    saved must be recovered idempotently: re-registering reuses the same file."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "metrics.json").write_text('{"accuracy": 0.9}', encoding="utf-8")
    registry = ArtifactRegistry(tmp_path / "artifacts")
    grant = WorkspaceGrant(root=str(workspace), source=WorkspaceSourceKind.LOCAL, access=WorkspaceAccess(read_paths=['.'], write_paths=['.']))
    candidate = ArtifactCandidate(
        kind="experiment_result",
        path="metrics.json",
        media_type="application/json",
        summary="metrics", output_name="metrics",
    )
    kwargs = dict(
        grant=grant,
        producer=AgentOwner.EXPERIMENT,
        run_id="run_x",
        task_id="task_x",
        attempt_number=1,
        index=1,
        existing_ids=set(),
    )

    first = registry.register(candidate, **kwargs)
    # Simulate the crash: the formal dir exists but the run index did not persist.
    second = registry.register(candidate, **kwargs)

    assert second.id == first.id
    assert second.sha256 == first.sha256
    assert Path(second.uri.removeprefix("file://")).is_file()


def test_import_identity_preserves_kind_and_source_provenance(tmp_path):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4 evidence")
    registry = ArtifactRegistry(tmp_path / "artifacts")
    base = dict(uri=str(source), kind="literature_pdf", media_type="application/pdf", summary="Paper")
    first = registry.register_import(
        ArtifactImport(**base, metadata={"paper_artifact_id": "artifact_source_a"}),
        run_id="run_import",
    )
    repeated = registry.register_import(
        ArtifactImport(**base, metadata={"paper_artifact_id": "artifact_source_a"}),
        run_id="run_import",
    )
    other = registry.register_import(
        ArtifactImport(**base, metadata={"paper_artifact_id": "artifact_source_b"}),
        run_id="run_import",
    )
    other_kind = registry.register_import(
        ArtifactImport(**{**base, "kind": "data"}, metadata={"paper_artifact_id": "artifact_source_a"}),
        run_id="run_import",
    )
    assert first == repeated
    assert len({first.id, other.id, other_kind.id}) == 3
    assert first.metadata == {"source_type": "import", "paper_artifact_id": "artifact_source_a"}


@pytest.mark.parametrize("renamed", [False, True])
def test_import_reuse_rejects_tampered_frozen_copy(tmp_path, renamed):
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4 evidence")
    registry = ArtifactRegistry(tmp_path / "artifacts")
    spec = ArtifactImport(uri=str(source), kind="paper", media_type="application/pdf", summary="Paper")
    first = registry.register_import(spec, run_id="run_import")
    Path(first.uri.removeprefix("file://")).write_bytes(b"tampered")
    if renamed:
        source = source.rename(tmp_path / "renamed.pdf")
        spec = spec.model_copy(update={"uri": str(source)})
    with pytest.raises(ArtifactRegistrationError, match="hash changed"):
        registry.register_import(spec, run_id="run_import")


def test_import_detects_source_mutation_during_copy(tmp_path, monkeypatch):
    import resagent2_orchestrator.artifacts as implementation
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4 evidence")
    registry = ArtifactRegistry(tmp_path / "artifacts")
    original_copy = implementation.shutil.copyfile

    def changing_copy(original, destination):
        original.write_bytes(b"changed before copy")
        return original_copy(original, destination)

    monkeypatch.setattr(implementation.shutil, "copyfile", changing_copy)
    with pytest.raises(ArtifactRegistrationError, match="changed while freezing"):
        registry.register_import(
            ArtifactImport(uri=str(source), kind="paper", media_type="application/pdf", summary="Paper"),
            run_id="run_import",
        )
    assert not list((tmp_path / "artifacts" / "run_import").glob("artifact_import_*"))


def test_import_reuses_frozen_file_when_caller_renames_source(tmp_path, monkeypatch):
    import resagent2_orchestrator.artifacts as implementation

    source = tmp_path / "paper.pdf"
    original = b"%PDF-1.4 evidence"
    source.write_bytes(original)
    registry = ArtifactRegistry(tmp_path / "artifacts")
    spec = ArtifactImport(uri=str(source), kind="paper", media_type="application/pdf", summary="Paper")
    first = registry.register_import(spec, run_id="run_import")
    renamed = source.rename(tmp_path / "renamed.pdf")

    def unexpected_copy(*args):
        raise OSError(28, "No space left on device")

    # A renamed but otherwise identical input must not copy or alter a frozen Ref.
    monkeypatch.setattr(implementation.shutil, "copyfile", unexpected_copy)
    second = registry.register_import(
        spec.model_copy(update={"uri": str(renamed)}), run_id="run_import",
    )
    assert second == first
    frozen = Path(first.uri.removeprefix("file://"))
    assert frozen.read_bytes() == original
    assert list(frozen.parent.iterdir()) == [frozen]


def test_failed_new_import_preserves_existing_frozen_inputs(tmp_path, monkeypatch):
    import resagent2_orchestrator.artifacts as implementation

    source = tmp_path / "paper.pdf"
    original = b"%PDF-1.4 evidence"
    source.write_bytes(original)
    registry = ArtifactRegistry(tmp_path / "artifacts")
    spec = ArtifactImport(uri=str(source), kind="paper", media_type="application/pdf", summary="Paper")
    first = registry.register_import(spec, run_id="run_import")
    frozen = Path(first.uri.removeprefix("file://"))
    source.write_bytes(b"%PDF-1.4 replacement")

    def failed_copy(*args):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(implementation.shutil, "copyfile", failed_copy)
    with pytest.raises(OSError, match="No space left"):
        registry.register_import(spec, run_id="run_import")

    assert frozen.read_bytes() == original
    assert list(frozen.parent.parent.iterdir()) == [frozen.parent]


def test_task_artifact_ids_support_longest_task_identity_without_collisions(tmp_path):
    registry = ArtifactRegistry(tmp_path / "artifacts")
    candidate = ArtifactCandidate(
        kind="experiment_result", path="metrics.json", media_type="application/json",
        summary="Metrics", content="{}",
    )
    first_task = TypeAdapter(TaskId).validate_python("task_" + "x" * 127 + "a")
    second_task = TypeAdapter(TaskId).validate_python("task_" + "x" * 127 + "b")
    scopes = [(first_task, 1, 1), (second_task, 1, 1), (first_task, 2, 1), (first_task, 1, 2)]
    refs = [
        registry.register(
            candidate, grant=None, producer=AgentOwner.EXPERIMENT, run_id="run_long",
            task_id=task_id, attempt_number=attempt, index=index, existing_ids=set(),
        )
        for task_id, attempt, index in scopes
    ]
    assert len({ref.id for ref in refs}) == len(scopes)
    for ref, (task_id, attempt, _) in zip(refs, scopes):
        assert TypeAdapter(ArtifactId).validate_python(ref.id) == ref.id
        assert ref.task_id == task_id and ref.attempt_number == attempt
        assert Path(ref.uri.removeprefix("file://")).read_text() == "{}"
    recovered = registry.register(
        candidate, grant=None, producer=AgentOwner.EXPERIMENT, run_id="run_long",
        task_id=first_task, attempt_number=1, index=1, existing_ids=set(),
    )
    assert recovered == refs[0]

    short = registry.register(
        candidate, grant=None, producer=AgentOwner.EXPERIMENT, run_id="run_short",
        task_id="task_x", attempt_number=1, index=1, existing_ids=set(),
    )
    assert short.id == "artifact_x_1_1"


def test_task_artifact_id_is_validated_before_creating_files(tmp_path):
    registry = ArtifactRegistry(tmp_path / "artifacts")
    candidate = ArtifactCandidate(
        kind="experiment_result", path="metrics.json", media_type="application/json",
        summary="Metrics", content="{}",
    )
    with pytest.raises(ValidationError):
        registry.register(
            candidate, grant=None, producer=AgentOwner.EXPERIMENT, run_id="run_invalid",
            task_id="task_bad.name", attempt_number=1, index=1, existing_ids=set(),
        )
    assert not (registry.root / "run_invalid").exists()
