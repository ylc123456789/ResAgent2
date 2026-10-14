"""Exercise durable allocation and returned-result recovery at actual write boundaries."""
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from resagent2_contracts import (
    AgentOwner, AgentResult, ArtifactCandidate, ArtifactImport, ResearchRequest,
    RunBudget, RunPermissions, TaskProposal, WorkflowProposal, WorkspaceGrant,
    WorkflowAgentDefinition, WorkflowAgentRegistry,
    WorkRequest, WorkRequestDraft,
)
from resagent2_orchestrator import (
    ArtifactRegistry, JsonRunStore, ModuleBinding, ResearchRun, WorkflowScheduler,
    ScientificArtifactRegistration,
    ResearchController, DeterministicWorkInterpreter,
)
from resagent2_orchestrator import artifacts as artifacts_module


class ProcessCrash(BaseException):
    pass


def setup_run(tmp_path):
    now = datetime.now(UTC)
    store = JsonRunStore(tmp_path / "state")
    run = ResearchRun(
        run_id="run_recovery", request=ResearchRequest(
            goal="Recover original material", permissions=RunPermissions(),
            budget=RunBudget(max_llm_calls=10, timeout_seconds=60),
        ), status="running", created_at=now, updated_at=now,
    )
    store.save(run)
    return store, ArtifactRegistry(tmp_path / "artifacts", store)


@pytest.mark.parametrize("entry", ["task", "import", "scientific", "system", "final"])
@pytest.mark.parametrize("boundary", ["allocated", "frozen", "registered"])
def test_interrupted_registration_reuses_preallocated_identity(tmp_path, monkeypatch, entry, boundary):
    store, registry = setup_run(tmp_path)
    source = tmp_path / "source.txt"
    source.write_text("Exact original bytes.\n", encoding="utf-8")

    def register(current):
        if entry == "task":
            return current.register(
                ArtifactCandidate(kind="experiment_result", path=source.name,
                                  media_type="text/plain", summary="Original result"),
                grant=WorkspaceGrant(root=str(tmp_path), access={"read_paths": ["."], "write_paths": ["."]}, source="local"),
                producer=AgentOwner.EXPERIMENT, run_id="run_recovery", task_id="task_result",
                attempt_number=1, index=1, operation_key="original",
            )
        if entry == "import":
            return current.register_import(ArtifactImport(uri=str(source), kind="literature_paper",
                media_type="text/plain", summary="Original paper"), run_id="run_recovery", operation_key="original")
        if entry == "scientific":
            return current.register_scientific(ArtifactCandidate(kind="literature_paper", path="paper.md",
                media_type="text/plain", summary="Original paper", content="Exact original bytes.\n"),
                run_id="run_recovery", session_id="session_scientific", operation_key="original")
        if entry == "system":
            return current.register_system_artifact(ArtifactCandidate(kind="conclusion_requirements", path="requirements.json",
                media_type="application/json", summary="Original requirements", content="Exact original bytes.\n"),
                run_id="run_recovery", source_type="conclusion_requirement", operation_key="original")
        return current.register_final_report(ArtifactCandidate(kind="final_report", path="final_report.md",
            media_type="text/markdown", summary="Final report", metadata={"source_type": "final_report"}),
            "Exact original bytes.\n", run_id="run_recovery")

    with monkeypatch.context() as patch:
        if boundary == "allocated":
            patch.setattr(artifacts_module.tempfile, "mkdtemp", lambda **kwargs: (_ for _ in ()).throw(ProcessCrash()))
        elif boundary == "frozen":
            original = artifacts_module.os.replace
            def freeze_then_crash(source, destination):
                original(source, destination)
                if Path(destination).name.startswith("artifact_"):
                    raise ProcessCrash()
            patch.setattr(artifacts_module.os, "replace", freeze_then_crash)
        else:
            original = store.save
            def register_then_crash(run):
                original(run)
                if run.artifacts:
                    raise ProcessCrash()
            patch.setattr(store, "save", register_then_crash)
        with pytest.raises(ProcessCrash):
            register(registry)

    pending = next(iter(store.load("run_recovery").pending_registrations.values()))
    recovered = register(ArtifactRegistry(registry.root, store))
    assert recovered == pending.ref
    assert store.load("run_recovery").artifacts == {recovered.id: recovered}
    assert Path(url2pathname(urlparse(recovered.uri).path)).read_bytes() == b"Exact original bytes.\n"


@pytest.mark.parametrize("deleted", [False, True])
def test_registered_file_recovery_uses_frozen_snapshot_when_source_changes(tmp_path, deleted):
    store, registry = setup_run(tmp_path)
    source = tmp_path / "result.txt"
    source.write_text("Original", encoding="utf-8")
    candidate = ArtifactCandidate(kind="experiment_result", path=source.name,
                                  media_type="text/plain", summary="Original result")
    kwargs = dict(grant=WorkspaceGrant(root=str(tmp_path), access={"read_paths": ["."], "write_paths": ["."]}, source="local"),
                  producer=AgentOwner.EXPERIMENT, run_id="run_recovery", task_id="task_result",
                  attempt_number=1, index=1, operation_key="original")
    original = registry.register(candidate, **kwargs)
    source.unlink() if deleted else source.write_text("Changed", encoding="utf-8")
    recovered = ArtifactRegistry(registry.root, store).register(candidate, **kwargs)
    assert recovered == original
    assert Path(url2pathname(urlparse(recovered.uri).path)).read_text() == "Original"


@pytest.mark.parametrize("boundary", ["before_report", "after_report_binding", "after_first_ack"])
def test_returned_result_recovery_does_not_call_agent_or_allocate_outputs_again(tmp_path, monkeypatch, boundary):
    store, registry = setup_run(tmp_path)
    class Port:
        calls = 0
        def invoke(self, request):
            self.calls += 1
            return AgentResult(status="completed", report="Exact original report.  \n", artifacts=[
                ArtifactCandidate(kind="experiment_result", path=f"result_{index}.txt", media_type="text/plain",
                                  summary=f"Result {index}", content=str(index), output_name=f"result_{index}")
                for index in range(2)
            ])
    port = Port()
    scheduler = WorkflowScheduler(bindings={"experiment": ModuleBinding(owner=AgentOwner.EXPERIMENT, port=port)},
                                  store=store, artifact_root=registry.root, data_root=tmp_path / "data")
    scheduler.accept_proposal("run_recovery", WorkflowProposal(work_request_id="work_original", tasks=[
        TaskProposal(id="task_result", work_request_id="work_original", workflow_agent_kind="experiment",
                     instruction="Return both results"),
    ]))
    with monkeypatch.context() as patch:
        if boundary == "before_report":
            patch.setattr(scheduler, "_persist_attempt_report", lambda *args: (_ for _ in ()).throw(ProcessCrash()))
        elif boundary == "after_report_binding":
            original = scheduler._save
            def save_then_crash(run):
                original(run)
                if run.workflow.tasks[0].attempts[0].report_artifact_id:
                    raise ProcessCrash()
            patch.setattr(scheduler, "_save", save_then_crash)
        else:
            original = scheduler.artifact_registry.acknowledge_registration
            def acknowledge_then_crash(key, **kwargs):
                original(key, **kwargs)
                if ":output:" in key:
                    raise ProcessCrash()
            patch.setattr(scheduler.artifact_registry, "acknowledge_registration", acknowledge_then_crash)
        with pytest.raises(ProcessCrash):
            scheduler.execute_task("run_recovery", "task_result")
    interrupted = store.load("run_recovery")
    outputs = interrupted.workflow.tasks[0].attempts[0].artifact_ids.copy()
    assert len(outputs) == 2
    scheduler._recover_interrupted_attempts_in_place(interrupted)
    recovered = store.load("run_recovery")
    attempt = recovered.workflow.tasks[0].attempts[0]
    assert port.calls == 1 and attempt.artifact_ids == outputs
    assert attempt.report == "Exact original report.  \n" and attempt.report_artifact_id
    assert len(recovered.artifacts) == 3 and recovered.pending_module_results == {}
    assert recovered.pending_registrations == {}


def test_scheduler_keeps_artifacts_registered_during_agent_invocation(tmp_path):
    store, registry = setup_run(tmp_path)
    class Port:
        def invoke(self, request):
            ref = registry.register(ArtifactCandidate(kind="experiment_result", path="live.txt", media_type="text/plain",
                summary="Tool registered result", content="Live material"), grant=None, producer=request.agent,
                run_id=request.run_id, task_id=request.task_id, attempt_number=request.attempt_number,
                index=1, operation_key="live:output")
            return AgentResult(status="completed", report="Original report", artifacts=[ref])
    scheduler = WorkflowScheduler(bindings={"experiment": ModuleBinding(owner=AgentOwner.EXPERIMENT, port=Port())},
                                  store=store, artifact_root=registry.root, data_root=tmp_path / "data")
    scheduler.accept_proposal("run_recovery", WorkflowProposal(work_request_id="work_original", tasks=[
        TaskProposal(id="task_result", work_request_id="work_original", workflow_agent_kind="experiment",
                     instruction="Keep live tool registration"),
    ]))
    result = scheduler.execute_task("run_recovery", "task_result")
    attempt = result.workflow.tasks[0].attempts[0]
    assert attempt.status == "completed" and len(attempt.artifact_ids) == 1
    assert result.artifacts[attempt.artifact_ids[0]].summary == "Tool registered result"


def test_scientific_unfrozen_allocation_grants_no_ref_and_is_consumed_after_receipt(tmp_path, monkeypatch):
    store, registry = setup_run(tmp_path)
    port = ScientificArtifactRegistration(registry, store)
    with monkeypatch.context() as patch:
        patch.setattr(artifacts_module.tempfile, "mkdtemp", lambda **kwargs: (_ for _ in ()).throw(ProcessCrash()))
        with pytest.raises(ProcessCrash):
            port.register_scientific(ArtifactCandidate(kind="literature_paper", path="paper.md", media_type="text/plain",
                summary="Paper", content="Exact bytes"), run_id="run_recovery", session_id="session_scientific",
                operation_key="session:session_scientific:turn:0:call:fetch", output_position=1)
    allocated = next(iter(store.load("run_recovery").pending_registrations.values())).ref.id
    assert port.recover_registration(run_id="run_recovery", operation_key="session:session_scientific:turn:0:call:fetch") == []
    assert port.resolve(allocated, run_id="run_recovery", session_id="session_scientific") is None
    port.acknowledge_registration(run_id="run_recovery", operation_key="session:session_scientific:turn:0:call:fetch")
    run = store.load("run_recovery")
    assert run.pending_registrations == {} and run.artifacts == {} and run.scientific_artifact_ids == []


@pytest.mark.parametrize("corruption", ["changed", "deleted"])
def test_registered_material_corruption_is_not_treated_as_unused_allocation(tmp_path, corruption):
    store, registry = setup_run(tmp_path)
    port = ScientificArtifactRegistration(registry, store)
    ref = port.register_scientific(ArtifactCandidate(kind="literature_paper", path="paper.md", media_type="text/plain",
        summary="Paper", content="Exact bytes"), run_id="run_recovery", session_id="session_scientific",
        operation_key="session:session_scientific:turn:0:call:fetch", output_position=1)
    path = Path(url2pathname(urlparse(ref.uri).path))
    path.unlink() if corruption == "deleted" else path.write_text("Changed", encoding="utf-8")
    for operation in (port.recover_registration, port.acknowledge_registration):
        with pytest.raises(ValueError, match="missing|sha256"):
            operation(run_id="run_recovery", operation_key="session:session_scientific:turn:0:call:fetch")
    assert store.load("run_recovery").pending_registrations
    assert port.resolve(ref.id, run_id="run_recovery", session_id="session_scientific") is None


@pytest.mark.parametrize("entry", ["import", "paper"])
def test_initialization_recovers_original_identity_before_first_agent_call(tmp_path, monkeypatch, entry):
    from resagent2_components.literature import LiteraturePaper, PreparedLiteratureImport
    class StopAfterPreparation(BaseException):
        pass
    class Port:
        requests = []
        def invoke(self, request):
            self.requests.append(request)
            raise StopAfterPreparation()
    port = Port()
    store = JsonRunStore(tmp_path / "state")
    scheduler = WorkflowScheduler(bindings={}, store=store, artifact_root=tmp_path / "artifacts")
    controller = ResearchController(scientific_port=port, compiler=None, scheduler=scheduler,
        registry=WorkflowAgentRegistry(definitions=[WorkflowAgentDefinition(workflow_agent_kind="experiment")]),
        interpreter=DeterministicWorkInterpreter())
    source = tmp_path / "original.md"
    source.write_text("Original imported bytes", encoding="utf-8")
    request = ResearchRequest(goal="Recover preparation", permissions=RunPermissions(), budget=RunBudget(max_llm_calls=10, timeout_seconds=60),
        input_artifacts=[ArtifactImport(uri=str(source), kind="data", media_type="text/plain", summary="Original input")] if entry == "import" else [])
    literature = [PreparedLiteratureImport(paper=LiteraturePaper(paper_id="arxiv:1706.03762v7", title="Original paper",
        source_url="https://arxiv.org/abs/1706.03762v7", abstract="Original abstract"))] if entry == "paper" else []
    original_replace = artifacts_module.os.replace
    with monkeypatch.context() as patch:
        def interrupt_before_freeze(source, destination):
            if Path(destination).name.startswith("artifact_"):
                raise ProcessCrash()
            return original_replace(source, destination)
        patch.setattr(artifacts_module.os, "replace", interrupt_before_freeze)
        with pytest.raises(ProcessCrash):
            controller.create_run("run_initialization", request, literature=literature)
    interrupted = store.load("run_initialization")
    ref = next(iter(interrupted.pending_registrations.values())).ref
    assert port.requests == [] and not interrupted.initialization.complete
    with pytest.raises(StopAfterPreparation):
        controller.run_until_stable("run_initialization")
    recovered = store.load("run_initialization")
    assert recovered.initialization.complete and recovered.pending_registrations == {}
    assert recovered.artifacts[ref.id] == ref
    assert len(port.requests) == 1 and ref in port.requests[0].input_artifacts


@pytest.mark.parametrize("boundary", ["allocated", "frozen"])
def test_final_receiving_error_keeps_durable_registration_facts(tmp_path, monkeypatch, boundary):
    from e2e.native_fixtures import tool_turns
    from resagent2_runtime import JsonSessionStore, ScriptedLLMClient
    from resagent2_scientific import ScientificAgent
    store = JsonRunStore(tmp_path / "state")
    scheduler = WorkflowScheduler(bindings={}, store=store, artifact_root=tmp_path / "artifacts")
    scientific = ScientificAgent(ScriptedLLMClient(tool_turns([{"tool": "finish", "arguments": {
        "report": "Original conclusion", "artifacts": [{"kind": "scientific_opinion", "path": "opinion.json",
            "media_type": "application/json", "summary": "Opinion",
            "content": '{"verdict":"inconclusive","statement":"No research execution was needed"}'}],
    }}])), store=JsonSessionStore(tmp_path / "sessions"))
    controller = ResearchController(scientific_port=scientific, compiler=None, scheduler=scheduler,
        registry=WorkflowAgentRegistry(definitions=[WorkflowAgentDefinition(workflow_agent_kind="experiment")]),
        interpreter=DeterministicWorkInterpreter())
    if boundary == "allocated":
        original = store.save
        def save_then_error(run):
            original(run)
            if "final_report" in run.pending_registrations:
                raise OSError("Receiving interrupted after final allocation")
        monkeypatch.setattr(store, "save", save_then_error)
    else:
        original = scheduler.artifact_registry._freeze
        def freeze_then_error(candidate, **kwargs):
            ref = original(candidate, **kwargs)
            if candidate.kind == "final_report":
                raise OSError("Receiving interrupted after final freezing")
            return ref
        monkeypatch.setattr(scheduler.artifact_registry, "_freeze", freeze_then_error)
    # Allow the failure state itself to be saved after the one injected fault.
    if boundary == "allocated":
        triggered = False
        def fail_once(run):
            nonlocal triggered
            if triggered:
                return original(run)
            if "final_report" in run.pending_registrations:
                triggered = True
            return save_then_error(run)
        monkeypatch.setattr(store, "save", fail_once)
    run = controller.create_run("run_final_receiving", ResearchRequest(goal="Preserve final registration",
        permissions=RunPermissions(), budget=RunBudget(max_llm_calls=5, timeout_seconds=60)))
    assert run.status == "failed" and "final_report" in run.pending_registrations
    pending = run.pending_registrations["final_report"].ref
    assert (pending.id in run.artifacts) == (boundary == "frozen")
    assert run.final_report_artifact_id is None and run.final_opinion is None
    assert store.load(run.run_id).pending_registrations["final_report"].ref == pending


@pytest.mark.parametrize("boundary", ["allocated", "frozen", "accepted"])
def test_compiler_result_is_reused_after_workflow_registration_interruption(tmp_path, monkeypatch, boundary):
    from resagent2_orchestrator import CompilationResult
    store, registry = setup_run(tmp_path)
    now = datetime.now(UTC)
    run = store.load("run_recovery")
    run.work_requests = [WorkRequest(id="work_original", run_id=run.run_id, scientific_session_id="session_scientific",
        request=WorkRequestDraft(objective='Deliver measurements', tasks=[{'key': 'work', 'instruction': 'Deliver measurements'}]),
        status="requested", created_at=now, updated_at=now)]
    store.save(run)
    class Compiler:
        calls = 0
        def compile(self, request, **kwargs):
            self.calls += 1
            assert self.calls == 1, "Recover the original accepted compiler output without another model call"
            return CompilationResult(WorkflowProposal(work_request_id=request.id, tasks=[TaskProposal(
                id="task_original", work_request_id=request.id, workflow_agent_kind="experiment",
                instruction="Deliver the measurement", output_names=["metrics"],
            )]))
    class Port:
        def invoke(self, request):
            return AgentResult(status="completed", report="Original measurement report", artifacts=[ArtifactCandidate(
                kind="experiment_result", path="result.json", media_type="application/json", summary="Measured result",
                output_name="metrics", content='{"accuracy": 0.8}',
            )])
    compiler = Compiler()
    scheduler = WorkflowScheduler(bindings={"experiment": ModuleBinding(owner=AgentOwner.EXPERIMENT, port=Port())},
                                  store=store, artifact_root=registry.root, data_root=tmp_path / "data")
    controller = ResearchController(scientific_port=None, compiler=compiler, scheduler=scheduler,
        registry=WorkflowAgentRegistry(definitions=[WorkflowAgentDefinition(workflow_agent_kind="experiment")]),
        interpreter=DeterministicWorkInterpreter())
    with monkeypatch.context() as patch:
        if boundary == "allocated":
            original = store.save
            def save_then_crash(run):
                original(run)
                if any(pending.ref.kind == "acceptance_requirements" for pending in run.pending_registrations.values()):
                    raise ProcessCrash()
            patch.setattr(store, "save", save_then_crash)
        elif boundary == "frozen":
            original = scheduler.artifact_registry._freeze
            def freeze_then_crash(candidate, **kwargs):
                ref = original(candidate, **kwargs)
                if candidate.kind == "acceptance_requirements":
                    raise ProcessCrash()
                return ref
            patch.setattr(scheduler.artifact_registry, "_freeze", freeze_then_crash)
        else:
            original = scheduler.accept_proposal
            def accept_then_crash(*args):
                original(*args)
                raise ProcessCrash()
            patch.setattr(scheduler, "accept_proposal", accept_then_crash)
        with pytest.raises(ProcessCrash):
            controller._execute_work_request(run.run_id)
    interrupted = store.load(run.run_id)
    assert interrupted.pending_compilation.tasks[0].id == "task_original" and compiler.calls == 1
    expected = (interrupted.workflow.tasks[0].acceptance_ref if boundary == "accepted" else
                next(pending.ref for pending in interrupted.pending_registrations.values()
                     if pending.ref.kind == "acceptance_requirements"))
    restored_scheduler = WorkflowScheduler(bindings=scheduler.bindings, store=JsonRunStore(store.root),
        artifact_root=registry.root, data_root=tmp_path / "data")
    restored = ResearchController(scientific_port=None, compiler=compiler, scheduler=restored_scheduler,
        registry=controller.registry, interpreter=DeterministicWorkInterpreter())
    result = restored._execute_work_request(run.run_id)
    assert compiler.calls == 1 and result.pending_compilation is None
    assert result.workflow.tasks[0].acceptance_ref == expected
    assert result.workflow.tasks[0].id == "task_original" and result.workflow.tasks[0].status == "completed"
    assert result.pending_registrations == {}


def test_rejected_compilation_does_not_replace_the_next_work_request_graph(tmp_path):
    from e2e.native_fixtures import tool_turns
    from resagent2_orchestrator import CompilationResult
    from resagent2_runtime import JsonSessionStore, ScriptedLLMClient
    from resagent2_scientific import ScientificAgent
    class Compiler:
        work_ids = []
        def compile(self, request, **kwargs):
            self.work_ids.append(request.id)
            first = len(self.work_ids) == 1
            return CompilationResult(WorkflowProposal(work_request_id=request.id, tasks=[TaskProposal(
                id="task_rejected" if first else "task_accepted", work_request_id=request.id,
                workflow_agent_kind="experiment", instruction="Execute the current work request",
                input_artifacts=["artifact_missing"] if first else [],
            )]))
    class Worker:
        def invoke(self, request):
            assert request.task_id == "task_accepted"
            return AgentResult(status="completed", report="The new work request completed")
    actions = [{"tool": "request_work", "arguments": {
        "assessment": {"statement": f"Request {index}"}, "work_request": {'objective': f'Run independent request {index}', 'tasks': [{'key': 'work', 'instruction': f'Run independent request {index}'}]},
    }} for index in range(2)]
    actions.append({"tool": "finish", "arguments": {"report": "The replacement request completed", "artifacts": [{
        "kind": "scientific_opinion", "path": "opinion.json", "media_type": "application/json", "summary": "Conclusion",
        "content": '{"verdict":"inconclusive","statement":"The second request completed","limitations":["The first work graph was rejected"]}',
    }]}})
    compiler = Compiler()
    scheduler = WorkflowScheduler(bindings={"experiment": ModuleBinding(owner=AgentOwner.EXPERIMENT, port=Worker())},
        store=JsonRunStore(tmp_path / "state"), artifact_root=tmp_path / "artifacts", data_root=tmp_path / "data")
    controller = ResearchController(scientific_port=ScientificAgent(ScriptedLLMClient(tool_turns(actions)),
        store=JsonSessionStore(tmp_path / "sessions")), compiler=compiler, scheduler=scheduler,
        registry=WorkflowAgentRegistry(definitions=[WorkflowAgentDefinition(workflow_agent_kind="experiment")]),
        interpreter=DeterministicWorkInterpreter())
    run = controller.create_run("run_replacement_compilation", ResearchRequest(goal="Recover from a rejected work graph",
        permissions=RunPermissions(), budget=RunBudget(max_llm_calls=10, timeout_seconds=60)))
    assert run.status == "completed", run.terminal_error
    assert compiler.work_ids == [work.id for work in run.work_requests] and len(set(compiler.work_ids)) == 2
    assert [work.status.value for work in run.work_requests] == ["failed", "consumed"]
    assert run.pending_compilation is None
    assert [task.id for task in run.workflow.tasks] == ["task_accepted"]
    assert run.workflow.tasks[0].work_request_id == run.work_requests[1].id
