"""Tests for the Phase 7.5 ResearchController (DEVELOPMENT_PLAN §7.5)."""

from resagent2_contracts import RunPermissions, ExecutionLimits

from datetime import UTC, datetime
import json

import pytest

from resagent2_contracts import (
    AgentOwner,
    AgentResult,
    ArtifactCandidate,
    ConclusionRequirements,
    ControlSignal,
    DatasetRef,
    ErrorCode,
    ModuleError,
    ModuleStatus,
    QuestionDraft,
    RecordedAnswer,
    ResearchRequest,
    RunBudget,
    RunStatus,
    ScientificOpinion,
    ScientificVerdict,
    SessionRef,
    SessionStatus,
    TaskProposal,
    UserAnswer,
    WorkFeedback,
    WorkRecord,
    WorkRequest,
    WorkRequestDraft,
    WorkRequestStatus,
    WorkflowAgentDefinition,
    WorkflowAgentKind,
    WorkflowAgentRegistry,
    WorkflowPatch,
    WorkflowProposal,
)
from resagent2_orchestrator import (
    DeterministicWorkInterpreter,
    CompilationError,
    CompilationResult,
    DeterministicWorkflowCompiler,
    InMemoryRunStore,
    JsonRunStore,
    ModuleBinding,
    ResearchController,
    ResearchRun,
    ScriptedModulePort,
    WorkflowScheduler,
)
from resagent2_runtime import InMemorySessionStore, JsonSessionStore, ScriptedLLMClient
from resagent2_scientific import ScientificAgent
from resagent2_orchestrator.handoffs import read_json, system_artifact

NOW = datetime(2026, 8, 28, tzinfo=UTC)


def _context_material(context, ref):
    name = f"material_{ref.id}"
    assert context.included_sections.count(name) == 1
    lines = context.text.splitlines()
    payload = json.loads(lines[lines.index(f"## {name}") + 1])
    assert payload["artifact_id"] == ref.id
    assert payload["kind"] == ref.kind
    return payload["content"]


@pytest.fixture
def run_clock(monkeypatch):
    from datetime import timedelta
    from resagent2_orchestrator import controller as controller_module
    from resagent2_orchestrator import scheduler as scheduler_module

    class Clock:
        value = datetime.now(UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.value

        @classmethod
        def advance(cls, seconds):
            cls.value += timedelta(seconds=seconds)

    monkeypatch.setattr(controller_module, "datetime", Clock)
    monkeypatch.setattr(scheduler_module, "datetime", Clock)
    return Clock


def test_user_wait_is_excluded_after_restart_without_resetting_budget(tmp_path, run_clock):
    from datetime import timedelta
    from resagent2_orchestrator import OrchestrationError

    ask = {
        "tool": "ask_user",
        "arguments": {"assessment": {"statement": "Need a user decision"}, 'text': 'Prepare the resources, then confirm.', 'requested_fields': ['ready']},
    }

    class TimedClient(ScriptedLLMClient):
        def next_action(self, context, action_type):
            run_clock.advance(10)  # Real work before each pause/finish must still count.
            return super().next_action(context, action_type)

    def rebuild(actions):
        controller = _build_recoverable_controller(
            JsonRunStore(tmp_path / "runs"), JsonSessionStore(tmp_path / "sessions"), actions,
        )
        controller.scientific_port.llm_client = TimedClient(actions)
        return controller

    first = rebuild([ask])
    paused = first.create_run("run_wait", research_request())
    assert paused.remaining_timeout_seconds(run_clock.now()) == 50
    assert paused.llm_calls_used == 1
    run_clock.advance(2 * 86400)
    assert paused.remaining_timeout_seconds(run_clock.now()) == 50

    second = rebuild([ask])
    budgets = []
    original_run = second.scientific_port.invoke

    def capture(request):
        budgets.append((
            request.budget.max_llm_calls,
            request.budget.timeout_seconds,
        ))
        return original_run(request)

    second.scientific_port.invoke = capture
    answer = UserAnswer(
        question_id=paused.pending_question.id, values={"ready": "yes"},
        answered_at=run_clock.now() + timedelta(days=100),  # Not the trusted clock.
    )
    again = second.answer_question(paused.run_id, answer)
    assert again.status == RunStatus.PAUSED
    assert budgets == [(49, 50)]
    assert again.user_wait_seconds == 2 * 86400
    assert again.remaining_timeout_seconds(run_clock.now()) == 40
    assert again.llm_calls_used == 2
    assert again.scientific_session.id == paused.scientific_session.id
    before = second.scheduler.store.load(paused.run_id).model_dump_json()
    with pytest.raises(OrchestrationError, match="does not match"):
        second.answer_question(paused.run_id, answer)
    assert second.scheduler.store.load(paused.run_id).model_dump_json() == before

    run_clock.advance(3600)
    third = rebuild([finish_action()])
    completed = third.answer_question(again.run_id, UserAnswer(
        question_id=again.pending_question.id, values={"ready": "yes"},
        answered_at=run_clock.now(),
    ))
    assert completed.status == RunStatus.COMPLETED
    assert completed.user_wait_seconds == 2 * 86400 + 3600
    assert completed.remaining_timeout_seconds(run_clock.now()) == 30
    assert completed.llm_calls_used == 3


def test_task_wait_uses_same_clock_and_refreshes_resources(run_clock):
    class Source:
        refs = []

        def references(self):
            return list(self.refs)

    source = Source()
    controller = build_controller(
        actions=[request_work_action(), finish_action()], dataset_ref_source=source,
    )
    port = ScriptedModulePort([_task_question_result(), completed_result()])
    controller.scheduler.bindings[WorkflowAgentKind.EXPERIMENT] = ModuleBinding(
        owner=AgentOwner.EXPERIMENT, port=port,
    )
    paused = controller.create_run("run_task_wait", research_request())
    assert paused.status == RunStatus.PAUSED
    attempt = paused.workflow.tasks[0].attempts[0]
    run_clock.advance(86400)
    source.refs = [DatasetRef(dataset_id="new_data", relative_path="new-data")]
    completed = controller.answer_question(paused.run_id, UserAnswer(
        question_id=paused.pending_question.id,
        values={"dataset": "ready"}, answered_at=run_clock.now(),
    ))
    assert completed.status == RunStatus.COMPLETED
    assert completed.user_wait_seconds == 86400
    assert port.requests[-1].budget.max_llm_calls == 49
    assert port.requests[-1].budget.timeout_seconds == 60
    from resagent2_orchestrator.handoffs import read_json
    catalog = next(ref for ref in port.requests[-1].input_artifacts if ref.kind == "dataset_catalog")
    assert read_json(catalog)["datasets"] == [ref.model_dump(mode="json") for ref in source.refs]
    assert port.requests[-1].parent_session_id == attempt.session.id
    assert port.requests[-1].attempt_number == attempt.number
    assert port.requests[-1].output_dir == port.requests[0].output_dir
    assert len(completed.workflow.tasks[0].attempts) == 1


def test_ordinary_downtime_is_not_user_wait(run_clock):
    controller = build_controller(actions=[])
    now = run_clock.now()
    run = ResearchRun(
        run_id="run_downtime", request=research_request(), status=RunStatus.RUNNING,
        created_at=now, updated_at=now,
    )
    controller.scheduler.store.save(run)
    run_clock.advance(61)
    assert run.remaining_timeout_seconds(run_clock.now()) == 0
    failed = controller.run_until_stable(run.run_id)
    assert failed.status == RunStatus.FAILED
    assert failed.terminal_error.code == ErrorCode.TIMEOUT
    assert failed.user_wait_seconds == 0


@pytest.mark.parametrize("invalid", [-1, float("inf"), float("nan")])
def test_run_rejects_invalid_user_wait(invalid):
    from pydantic import ValidationError

    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        ResearchRun(
            run_id="run_wait_invalid", request=research_request(), status=RunStatus.RUNNING,
            created_at=now, updated_at=now, user_wait_seconds=invalid,
        )



def research_request() -> ResearchRequest:
    return ResearchRequest(goal='Evaluate the method', budget=RunBudget(max_llm_calls=50, timeout_seconds=60), permissions=RunPermissions(execute_commands=True, prepare_environment=True), execution_limits=ExecutionLimits(max_tasks=5, max_attempts_per_task=2))


def registry() -> WorkflowAgentRegistry:
    return WorkflowAgentRegistry(
        definitions=[
            WorkflowAgentDefinition(
                workflow_agent_kind=WorkflowAgentKind.EXPERIMENT,

            )
        ]
    )


def proposal(work_request_id: str) -> WorkflowProposal:
    return WorkflowProposal(
        work_request_id=work_request_id,
        tasks=[
            TaskProposal(
                id="task_experiment",
                work_request_id=work_request_id,
                workflow_agent_kind=WorkflowAgentKind.EXPERIMENT,
                instruction="Run the experiment",

            )
        ],
    )


def completed_result() -> AgentResult:
    return AgentResult(status=ModuleStatus.COMPLETED, report="done", session=SessionRef(id="session_task_child", module=AgentOwner.EXPERIMENT, state_uri="memory://session_task_child", status=SessionStatus.COMPLETED, created_at=NOW, updated_at=NOW))


def build_controller(
    *,
    actions: list[dict],
    store=None,
    dataset_ref_source=None,
) -> ResearchController:
    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([completed_result()]),
            )
        },
        store=store or InMemoryRunStore(),
    )
    compiler = DeterministicWorkflowCompiler(proposal("work_1"), patch=None)
    scientific = ScientificAgent(
        ScriptedLLMClient(actions),
        store=InMemorySessionStore(),
    )
    return ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=scientific,
        compiler=compiler,
        scheduler=scheduler,
        registry=registry(),
        dataset_ref_source=dataset_ref_source,
    )


def request_work_action() -> dict:
    return {
        "tool": "request_work",
        "arguments": {
            "assessment": {"statement": "need evidence"},
            "work_request": {
                "objective": "Run the experiment",
                "expected_evidence": ["accuracy"],
            },
        },
    }


def finish_action(verdict=ScientificVerdict.INCONCLUSIVE, limitations=()) -> dict:
    opinion = {"verdict": verdict.value, "statement": "done"}
    if limitations:
        opinion["limitations"] = list(limitations)
    return {"tool": "finish", "arguments": {"report": "Scientific conclusion", "artifacts": [ArtifactCandidate(kind="scientific_opinion", path="scientific_opinion.json", media_type="application/json", summary="Scientific conclusion", content=json.dumps(opinion)).model_dump(mode="json")]}}


def test_direct_conclusion_without_work() -> None:
    controller = build_controller(actions=[finish_action(ScientificVerdict.INCONCLUSIVE)])

    run = controller.create_run("run_direct", research_request())

    assert run.status == RunStatus.COMPLETED, run.terminal_error
    assert run.final_opinion is not None
    assert run.work_requests == []
    assert run.final_report_artifact_id == "artifact_final_report"
    report = run.artifacts[run.final_report_artifact_id]
    assert report.producer == AgentOwner.ORCHESTRATOR
    assert report.metadata == {"source_type": "final_report"}


def test_run_creation_freezes_grants_before_any_work_request(tmp_path):
    from resagent2_contracts import WorkspaceAccess, WorkspaceSpec

    controller = build_controller(actions=[finish_action()])
    original = WorkspaceSpec(
        workspace_id="ws_main", source_kind="local", location=str(tmp_path / "original"),
        access=WorkspaceAccess(read_paths=["src"], denied_paths=["src/private"]),
    )
    controller.scheduler.workspace_specs = {"ws_main": original}
    request = research_request().model_copy(update={"permissions": RunPermissions()})
    created = controller.create_run("run_frozen", request)
    assert not created.work_requests
    assert created.workspaces["ws_main"].source.access == original.access

    # Subsequent resolver configuration and caller-owned models are not authority
    # to expand an already persisted Run, even before its first task is accepted.
    request.permissions.execute_commands = True
    original.access.read_paths[:] = ["."]
    original.access.write_paths[:] = ["."]
    original.access.denied_paths.clear()
    controller.scheduler.workspace_specs = {"ws_main": WorkspaceSpec(
        workspace_id="ws_main", source_kind="local", location=str(tmp_path / "other"),
        access=WorkspaceAccess(read_paths=["."], write_paths=["."]),
    )}
    persisted = controller.scheduler.load(created.run_id)
    persisted.status = RunStatus.RUNNING
    controller.scheduler.store.save(persisted)
    controller.scheduler.accept_proposal(created.run_id, proposal("work_later"))
    controller.scheduler.run_until_stable(created.run_id)
    received = controller.scheduler.bindings[WorkflowAgentKind.EXPERIMENT].port.requests[0]
    assert received.workspace.root == str(tmp_path / "original")
    assert received.workspace.access.read_paths == ["src"]
    assert received.workspace.access.write_paths == []
    assert received.workspace.access.denied_paths == ["src/private"]
    assert not received.permissions.execute_commands
    assert not received.permissions.prepare_environment


def test_first_scientific_turn_recovers_from_bound_session_checkpoint() -> None:
    """A crash after runtime checkpointing cannot orphan the first session."""
    store = InMemoryRunStore()
    scientific = ScientificAgent(
        ScriptedLLMClient([finish_action(ScientificVerdict.INCONCLUSIVE)]),
        store=InMemorySessionStore(),
    )

    class _CrashAfterScientificCheckpoint:
        calls = 0

        def invoke(self, request):
            result = scientific.invoke(request)
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("simulated controller crash")
            return result

    scheduler = WorkflowScheduler(bindings={}, store=store)
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=_CrashAfterScientificCheckpoint(),
        compiler=DeterministicWorkflowCompiler(proposal("work_1"), patch=None),
        scheduler=scheduler,
        registry=registry(),
    )

    with pytest.raises(RuntimeError, match="simulated controller crash"):
        controller.create_run("run_first_turn_restart", research_request())

    persisted = store.load("run_first_turn_restart")
    assert persisted.scientific_session is not None
    assert persisted.scientific_session.id == "session_scientific_run_first_turn_restart"
    assert persisted.scientific_session.status == SessionStatus.ACTIVE

    recovered = controller.run_until_stable("run_first_turn_restart")
    assert recovered.status == RunStatus.COMPLETED
    assert recovered.scientific_session is not None
    assert recovered.scientific_session.id == persisted.scientific_session.id


def test_completion_gate_violations_are_persisted() -> None:
    class _InvalidCompletionPort:
        def invoke(self, request):
            return AgentResult(status="completed", report="Conclusion without its session or evidence")

    scheduler = WorkflowScheduler(bindings={}, store=InMemoryRunStore())
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=_InvalidCompletionPort(),
        compiler=DeterministicWorkflowCompiler(proposal("work_1"), patch=None),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.create_run("run_invalid_gate", research_request())

    assert run.status == RunStatus.FAILED
    assert any(item.code.value == "invalid_session" for item in run.completion_violations)
    assert scheduler.store.load(run.run_id).completion_violations == run.completion_violations


def test_single_work_cycle_completes() -> None:
    controller = build_controller(
        actions=[request_work_action(), finish_action()]
    )

    run = controller.create_run("run_cycle", research_request())

    assert run.status == RunStatus.COMPLETED, run.terminal_error
    assert run.final_opinion is not None
    assert len(run.work_requests) == 1
    assert run.work_requests[0].status.value == "consumed"
    # The WorkOutcome must carry the real execution summary, not the task goal.
    assert run.work_requests[0].outcome.tasks[0].summary == "done"


def _cycle_compiler():
    """A compiler that emits a proposal for the first request and a patch (adding
    a fresh task) for every subsequent request on the same workflow."""

    class _CycleCompiler:
        def compile(self, request, *, current, registry, limits, workspaces=None):
            if current is None:
                return CompilationResult(proposal(request.id))
            # A new request on an existing workflow becomes a patch adding one task.
            return CompilationResult(
                WorkflowPatch(
                    work_request_id=request.id,
                    based_on_revision=current.revision,
                    add_tasks=[
                        TaskProposal(
                            id=f"task_{request.id}",
                            work_request_id=request.id,
                            workflow_agent_kind=WorkflowAgentKind.EXPERIMENT,
                            instruction=f"Run for {request.id}",

                        )
                    ],
                )
            )

    return _CycleCompiler()


def test_multiple_serial_work_cycles(tmp_path) -> None:
    actions = [
        request_work_action(),
        request_work_action(),
        finish_action(),
    ]
    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([completed_result(), completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    scientific = ScientificAgent(ScriptedLLMClient(actions), store=InMemorySessionStore())
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=scientific,
        compiler=_cycle_compiler(),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.create_run("run_multi", research_request())

    assert run.status == RunStatus.COMPLETED, run.terminal_error
    assert len(run.work_requests) == 2
    assert [item.status.value for item in run.work_requests] == ["consumed", "consumed"]


def test_task_failure_then_request_alternative_work(tmp_path) -> None:
    failed = AgentResult(
        status=ModuleStatus.FAILED,
        report="crashed",
        error=ModuleError(
            code=ErrorCode.TOOL_FAILED,
            message="crashed",
            retryable=False,
        ),
    )
    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([failed, completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    actions = [
        request_work_action(),
        request_work_action(),
        finish_action(limitations=["a task failed"]),
    ]
    scientific = ScientificAgent(ScriptedLLMClient(actions), store=InMemorySessionStore())
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=scientific,
        compiler=_cycle_compiler(),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.create_run("run_failure", research_request())

    assert run.status == RunStatus.COMPLETED, run.terminal_error
    assert len(run.work_requests) == 2


def test_paused_question_then_answer_resumes() -> None:
    ask_action = {
        "tool": "ask_user",
        "arguments": {"assessment": {"statement": "Need a user decision"}, 'text': 'Which dataset?', 'requested_fields': ['dataset']},
    }
    controller = build_controller(
        actions=[ask_action, finish_action()],
    )

    paused = controller.create_run("run_paused", research_request())
    assert paused.status == RunStatus.PAUSED
    assert paused.pending_question is not None

    answer = UserAnswer(
        question_id=paused.pending_question.id,
        values={"dataset": "demo"},
        answered_at=NOW,
    )
    run = controller.answer_question("run_paused", answer)

    assert run.status == RunStatus.COMPLETED, run.terminal_error


def test_newly_registered_dataset_is_added_when_answer_resumes_run() -> None:
    class _DatasetSource:
        refs = []

        def references(self):
            return list(self.refs)

    source = _DatasetSource()
    ask_action = {
        "tool": "ask_user",
        "arguments": {"assessment": {"statement": "Need a user decision"}, 'text': 'Provision cifar10 and confirm when it is ready.', 'requested_fields': ['dataset_ready']},
    }
    controller = build_controller(
        actions=[ask_action, finish_action()],
        dataset_ref_source=source,
    )
    request = research_request()
    paused = controller.create_run("run_dataset_refresh", request)
    assert paused.dataset_refs == []
    assert paused.request == request

    source.refs = [DatasetRef(dataset_id="cifar10", relative_path="cifar10")]
    completed = controller.answer_question(
        paused.run_id,
        UserAnswer(
            question_id=paused.pending_question.id,
            values={"dataset_ready": "yes"},
            answered_at=NOW,
        ),
    )

    assert completed.status == RunStatus.COMPLETED
    assert completed.dataset_refs == source.refs
    assert completed.request == request




def test_catalog_refresh_survives_controller_restart_and_verbal_confirmation(tmp_path):
    from resagent2_components import (
        DatasetCatalog,
        ResourceLayout,
    )

    layout = ResourceLayout(resource_root=tmp_path / "resources")
    layout.dataset_root.mkdir(parents=True)
    ask = {
        "tool": "ask_user",
        "arguments": {"assessment": {"statement": "Need a user decision"}, 'text': 'Place and register demo, then confirm', 'requested_fields': ['dataset_ready']},
    }

    def rebuild(actions):
        controller = _build_recoverable_controller(
            JsonRunStore(tmp_path / "runs"),
            JsonSessionStore(tmp_path / "sessions"), actions,
        )
        controller.dataset_ref_source = DatasetCatalog(layout.dataset_root)
        controller.scientific_port.resource_layout = layout
        return controller

    original = research_request()
    first = rebuild([ask])
    paused = first.create_run("run_catalog", original)
    session_id = paused.scientific_session.id
    (layout.dataset_root / "catalog.json").write_text('{"demo": "demo"}', encoding="utf-8")

    second = rebuild([ask])
    still_missing = second.answer_question(paused.run_id, UserAnswer(
        question_id=paused.pending_question.id,
        values={"dataset_ready": "yes"}, answered_at=datetime.now(UTC),
    ))
    text = second.scientific_port.llm_client.contexts[0].text
    assert '"available_dataset_ids": []' in text
    assert '"unavailable_dataset_ids": ["demo"]' in text
    assert still_missing.status == RunStatus.PAUSED
    assert still_missing.pending_question.id != paused.pending_question.id

    (layout.dataset_root / "demo").mkdir()
    third = rebuild([finish_action()])
    completed = third.answer_question(paused.run_id, UserAnswer(
        question_id=still_missing.pending_question.id,
        values={"dataset_ready": "yes"}, answered_at=datetime.now(UTC),
    ))
    assert completed.status == RunStatus.COMPLETED
    assert completed.scientific_session.id == session_id
    assert completed.request == original
    assert completed.dataset_refs == DatasetCatalog(layout.dataset_root).references()
    assert '"available_dataset_ids": ["demo"]' in third.scientific_port.llm_client.contexts[0].text


def test_dataset_binding_cannot_be_remapped_during_run() -> None:
    class _DatasetSource:
        def references(self):
            return [DatasetRef(dataset_id="cifar10", relative_path="other")]

    controller = build_controller(
        actions=[finish_action()],
        dataset_ref_source=_DatasetSource(),
    )
    now = datetime.now(UTC)
    controller.scheduler.store.save(ResearchRun(
        run_id="run_dataset_remap", request=research_request(),
        dataset_refs=[DatasetRef(dataset_id="cifar10", relative_path="cifar10")],
        status=RunStatus.RUNNING, created_at=now, updated_at=now,
    ))
    with pytest.raises(ValueError, match="remapped during Run"):
        controller.run_until_stable("run_dataset_remap")


def _task_question_result() -> AgentResult:
    return AgentResult(
        status=ModuleStatus.NEEDS_USER_INPUT,
        report="Which dataset?",
        artifacts=[ArtifactCandidate(kind="question", path="question.json", media_type="application/json", summary="Question", content=QuestionDraft(text='Which dataset?', requested_fields=['dataset']).model_dump_json())], control=ControlSignal(action="ask_user", candidate_index=0),
        session=SessionRef(
            id="session_task_child",
            module=AgentOwner.EXPERIMENT,
            state_uri="memory://session_task_child",
            status=SessionStatus.PAUSED,
            created_at=NOW,
            updated_at=NOW,
        ),
    )


def test_task_question_resumes_same_attempt_via_controller() -> None:
    """A task-level question pauses the run; the answer resumes the same Attempt.

    This is the cross-layer fix for P1-1: the controller (not a separate
    scheduler answer path) routes the answer back to the paused task, which
    resumes on the same Attempt number instead of starting a new one.
    """
    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([_task_question_result(), completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    scientific = ScientificAgent(
        ScriptedLLMClient([request_work_action(), finish_action()]),
        store=InMemorySessionStore(),
    )
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=scientific,
        compiler=DeterministicWorkflowCompiler(proposal("work_1"), patch=None),
        scheduler=scheduler,
        registry=registry(),
    )

    paused = controller.create_run("run_task_question", research_request())

    assert paused.status == RunStatus.PAUSED
    assert paused.pending_question is not None
    assert paused.pending_question.task_id == "task_experiment"
    task = paused.workflow.tasks[0]
    assert task.status.value == "needs_user_input"
    assert task.attempts[0].status.value == "needs_user_input"
    assert task.attempts[0].finished_at is None

    answer = UserAnswer(
        question_id=paused.pending_question.id,
        values={"dataset": "demo"},
        answered_at=NOW,
    )
    run = controller.answer_question("run_task_question", answer)

    assert run.status == RunStatus.COMPLETED, run.terminal_error
    task = run.workflow.tasks[0]
    # The same Attempt resumed, not a new one.
    assert [attempt.number for attempt in task.attempts] == [1]
    assert task.attempts[0].status.value == "completed"
    assert run.work_requests[0].status.value == "consumed"
    resumed_request = scheduler.bindings[WorkflowAgentKind.EXPERIMENT].port.requests[-1]
    from resagent2_orchestrator.handoffs import read_json
    answers = [read_json(ref, RecordedAnswer) for ref in resumed_request.input_artifacts if ref.kind == "answer"]
    assert answers == run.answers
    assert answers[0].question_text == paused.pending_question.text
    assert answers[0].values == answer.values
    final_context = scientific.llm_client.contexts[-1]
    task_answer_ids = [ref.id for ref in resumed_request.input_artifacts if ref.kind == "answer"]
    assert all(f"material_{key}" not in final_context.included_sections for key in task_answer_ids)
    assert all(key in final_context.text for key in task_answer_ids)  # Discoverable, not replayed as a resume answer.


def test_task_question_resume_does_not_consume_attempt_budget() -> None:
    """max_attempts_per_task=1 must still allow a pause/resume round-trip."""
    request = ResearchRequest(goal='Evaluate', budget=RunBudget(max_llm_calls=50, timeout_seconds=60), permissions=RunPermissions(execute_commands=True, prepare_environment=True), execution_limits=ExecutionLimits(max_tasks=5, max_attempts_per_task=1))
    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([_task_question_result(), completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    scientific = ScientificAgent(
        ScriptedLLMClient([request_work_action(), finish_action()]),
        store=InMemorySessionStore(),
    )
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=scientific,
        compiler=DeterministicWorkflowCompiler(proposal("work_1"), patch=None),
        scheduler=scheduler,
        registry=registry(),
    )

    paused = controller.create_run("run_task_question_1", request)
    assert paused.status == RunStatus.PAUSED

    answer = UserAnswer(
        question_id=paused.pending_question.id,
        values={"dataset": "demo"},
        answered_at=NOW,
    )
    run = controller.answer_question("run_task_question_1", answer)

    assert run.status == RunStatus.COMPLETED, run.terminal_error
    assert [attempt.number for attempt in run.workflow.tasks[0].attempts] == [1]


def test_json_store_recovers_run_boundary(tmp_path) -> None:
    store = JsonRunStore(tmp_path / "state")
    controller = build_controller(
        actions=[request_work_action(), finish_action()],
        store=store,
    )
    controller.create_run("run_recover", research_request())

    recovered = JsonRunStore(tmp_path / "state").load("run_recover")
    assert recovered.status == RunStatus.COMPLETED
    assert recovered.final_opinion is not None


def test_compilation_failure_fails_run() -> None:
    class _FailingCompiler:
        def compile(self, request, *, current, registry, limits, workspaces=None):
            raise ValueError("compiler exploded")

    scheduler = WorkflowScheduler(
        bindings={},
        store=InMemoryRunStore(),
    )
    scientific = ScientificAgent(
        ScriptedLLMClient([request_work_action()]),
        store=InMemorySessionStore(),
    )
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=scientific,
        compiler=_FailingCompiler(),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.create_run("run_compile_fail", research_request())

    assert run.status == RunStatus.FAILED
    assert run.work_requests[0].status.value == "failed"


@pytest.mark.parametrize("accepted_status", [WorkRequestStatus.COMPILING, WorkRequestStatus.EXECUTING])
def test_compiling_restart_resumes_an_already_accepted_workflow(tmp_path, accepted_status) -> None:
    """Do not compile/apply a second graph after the acceptance crash window."""

    class _MustNotCompile:
        def compile(self, request, *, current, registry, limits, workspaces=None):
            raise AssertionError("accepted workflow must not be compiled again")

    completed_session = SessionRef(
        id="session_recovery",
        module=AgentOwner.SCIENTIFIC,
        state_uri="memory://session_recovery",
        status=SessionStatus.COMPLETED,
        created_at=NOW,
        updated_at=NOW,
    )

    class _FinishingPort:
        def invoke(self, request):
            feedback_ref = next(ref for ref in request.input_artifacts if ref.kind == "work_feedback")
            feedback = read_json(feedback_ref, WorkFeedback)
            assert feedback.work_request_id == "work_1"
            assert feedback.session_id == completed_session.id
            record_ref = next(ref for ref in request.input_artifacts if ref.id == feedback.work_record_artifact_id)
            record = read_json(record_ref, WorkRecord)
            assert record.work_outcome.tasks[0].task_id == "task_experiment"
            assert feedback_ref.id in request.resume_artifact_ids
            return AgentResult(status='completed', session=completed_session, llm_calls=1, report="Scientific conclusion", artifacts=[ArtifactCandidate(kind="scientific_opinion", path="opinion.json", media_type="application/json", summary="Conclusion", content=ScientificOpinion(verdict=ScientificVerdict.INCONCLUSIVE, statement='Execution completed without decisive evidence.').model_dump_json()), ArtifactCandidate(kind="observation_trace", path="observations.json", media_type="application/json", summary="Observed", content=json.dumps({"observed_artifact_ids": []}))])

    store = InMemoryRunStore()
    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([completed_result()]),
            )
        },
        store=store,
        artifact_root=tmp_path / "artifacts",
    )
    compiling = WorkRequest(
        id="work_1",
        run_id="run_accept_recovery",
        scientific_session_id=completed_session.id,
        request=WorkRequestDraft(
            objective="Run the experiment",
            expected_evidence=["metric"],
        ),
        status=WorkRequestStatus.COMPILING,
        created_at=NOW,
        updated_at=NOW,
    )
    request = research_request()
    request.execution_limits.max_tasks = 1
    store.save(
        ResearchRun(
            run_id="run_accept_recovery",
            request=request,
            status=RunStatus.RUNNING,
            scientific_session=completed_session.model_copy(update={"status": SessionStatus.PAUSED}),
            work_requests=[compiling],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    recovered_state = store.load("run_accept_recovery")
    recovered_state.conclusion_requirements_ref = system_artifact(
        scheduler.artifact_registry, recovered_state, "conclusion_requirements", ConclusionRequirements(),
    )
    store.save(recovered_state)
    # Simulate the crash: scheduler acceptance is durable, but the controller
    # has not yet persisted compiling -> executing.
    scheduler.accept_proposal(
        "run_accept_recovery",
        proposal("work_1"),
    )
    accepted = store.load("run_accept_recovery")
    accepted.work_requests[0].status = accepted_status
    if accepted_status == WorkRequestStatus.EXECUTING:
        accepted.work_requests[0].workflow_revision = accepted.workflow.revision
    store.save(accepted)
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=_FinishingPort(),
        compiler=_MustNotCompile(),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.run_until_stable("run_accept_recovery")

    assert run.status == RunStatus.COMPLETED, run.terminal_error
    assert run.work_requests[0].status == WorkRequestStatus.CONSUMED
    assert run.workflow.revision == 1


@pytest.mark.parametrize("previous_failed", [False, True])
def test_zero_task_slots_fail_before_compiler_and_preserve_history(tmp_path, previous_failed) -> None:
    class CountingCompiler:
        calls = 0

        def compile(self, request, **kwargs):
            self.calls += 1
            assert kwargs["current"] is None, "zero-slot round must not reach any compiler"
            return CompilationResult(proposal(request.id))

    first_result = (
        AgentResult(
            status=ModuleStatus.FAILED, report="Execution failed",
            error=ModuleError(code=ErrorCode.TOOL_FAILED, message="Original failure", retryable=False),
        )
        if previous_failed else completed_result()
    )
    scheduler = WorkflowScheduler(
        bindings={WorkflowAgentKind.EXPERIMENT: ModuleBinding(
            owner=AgentOwner.EXPERIMENT, port=ScriptedModulePort([first_result]),
        )},
        store=InMemoryRunStore(), artifact_root=tmp_path / "artifacts",
        data_root=tmp_path / "data",
    )
    compiler = CountingCompiler()
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=ScientificAgent(
            ScriptedLLMClient([request_work_action(), request_work_action()]),
            store=InMemorySessionStore(),
        ),
        compiler=compiler, scheduler=scheduler, registry=registry(),
    )
    request = research_request()
    request.execution_limits.max_tasks = 1
    run = controller.create_run("run_task_slots", request)

    assert run.status == RunStatus.FAILED
    assert run.terminal_error.code == ErrorCode.BUDGET_EXHAUSTED
    assert "No remaining task slots" in run.terminal_error.message
    assert run.work_requests[0].status == WorkRequestStatus.CONSUMED
    assert run.work_requests[-1].status == WorkRequestStatus.FAILED
    assert run.work_requests[-1].error == run.terminal_error
    assert compiler.calls == 1
    assert run.llm_calls_used == 2  # only the two Scientific turns
    assert len(run.workflow.tasks) == 1
    assert run.workflow.tasks[0].status.value == ("failed" if previous_failed else "completed")
    history = run.workflow.model_dump_json()

    # A restart from COMPILING before candidate acceptance must use the same
    # preflight, not loop into a zero-slot compiler call or leave work active.
    run.status = RunStatus.RUNNING
    run.terminal_error = None
    run.work_requests[-1].status = WorkRequestStatus.COMPILING
    run.work_requests[-1].error = None
    scheduler.store.save(run)
    resumed = controller.run_until_stable(run.run_id)
    assert resumed.status == RunStatus.FAILED
    assert resumed.terminal_error.code == ErrorCode.BUDGET_EXHAUSTED
    assert resumed.work_requests[-1].status == WorkRequestStatus.FAILED
    assert resumed.work_requests[-1].error == resumed.terminal_error
    assert resumed.llm_calls_used == 2
    assert compiler.calls == 1
    assert resumed.workflow.model_dump_json() == history


def test_second_work_outcome_contains_only_second_round_tasks() -> None:
    actions = [
        request_work_action(),
        request_work_action(),
        finish_action(),
    ]
    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([completed_result(), completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    scientific = ScientificAgent(ScriptedLLMClient(actions), store=InMemorySessionStore())
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=scientific,
        compiler=_cycle_compiler(),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.create_run("run_outcome_isolate", research_request())

    assert run.status == RunStatus.COMPLETED, run.terminal_error
    first = run.work_requests[0]
    second = run.work_requests[1]
    assert first.outcome.work_request_id == first.id
    assert [t.task_id for t in first.outcome.tasks] == ["task_experiment"]
    assert second.outcome.work_request_id == second.id
    assert [t.task_id for t in second.outcome.tasks] == ["task_work_2"]


def test_failed_task_appears_in_unresolved_then_is_reported() -> None:
    failed = AgentResult(
        status=ModuleStatus.FAILED,
        report="crashed",
        error=ModuleError(
            code=ErrorCode.TOOL_FAILED, message="crashed", retryable=False
        ),
    )
    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([failed, completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    actions = [
        request_work_action(),
        request_work_action(),
        finish_action(limitations=["a task failed"]),
    ]
    scientific = ScientificAgent(ScriptedLLMClient(actions), store=InMemorySessionStore())
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=scientific,
        compiler=_cycle_compiler(),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.create_run("run_unresolved", research_request())

    assert run.status == RunStatus.COMPLETED, run.terminal_error
    first_outcome = run.work_requests[0].outcome
    assert [t.task_id for t in first_outcome.tasks] == ["task_experiment"]
    assert first_outcome.tasks[0].status == "failed"


def test_forged_observed_artifact_is_rejected() -> None:
    class _ForgingPort:
        """A ScientificPort that reports an observed id that was never registered."""

        def __init__(self):
            from resagent2_scientific import ScientificAgent

            self.agent = ScientificAgent(
                ScriptedLLMClient([request_work_action(), finish_action()]),
                store=InMemorySessionStore(),
            )

        def invoke(self, request):
            result = self.agent.invoke(request)
            # Forge an extra observed id into the result.
            for artifact in result.artifacts:
                if artifact.kind == "observation_trace":
                    artifact.content = json.dumps({"observed_artifact_ids": ["artifact_fake"]})
            return result

    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=_ForgingPort(),
        compiler=DeterministicWorkflowCompiler(proposal("work_1"), patch=None),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.create_run("run_forged", research_request())

    assert run.status == RunStatus.FAILED
    assert run.scientific_observed_artifact_ids == []


def test_run_total_llm_budget_exhaustion() -> None:
    # A tiny run budget so the accumulated Scientific turns exceed it.
    tiny = ResearchRequest(goal='Evaluate', budget=RunBudget(max_llm_calls=1, timeout_seconds=60), permissions=RunPermissions(execute_commands=True, prepare_environment=True), execution_limits=ExecutionLimits(max_tasks=5, max_attempts_per_task=2))
    actions = [request_work_action(), request_work_action()]
    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([completed_result(), completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    scientific = ScientificAgent(ScriptedLLMClient(actions), store=InMemorySessionStore())
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=scientific,
        compiler=_cycle_compiler(),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.create_run("run_budget", tiny)

    assert run.status == RunStatus.FAILED


def test_answer_then_request_work_then_outcome_completes() -> None:
    ask_action = {
        "tool": "ask_user",
        "arguments": {"assessment": {"statement": "Need a user decision"}, 'text': 'Which dataset?', 'requested_fields': ['dataset']},
    }
    actions = [ask_action, request_work_action(), finish_action()]
    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    scientific = ScientificAgent(ScriptedLLMClient(actions), store=InMemorySessionStore())
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=scientific,
        compiler=DeterministicWorkflowCompiler(proposal("work_1"), patch=None),
        scheduler=scheduler,
        registry=registry(),
    )

    paused = controller.create_run("run_answer_work", research_request())
    assert paused.status == RunStatus.PAUSED

    answer = UserAnswer(
        question_id=paused.pending_question.id,
        values={"dataset": "demo"},
        answered_at=NOW,
    )
    run = controller.answer_question("run_answer_work", answer)

    assert run.status == RunStatus.COMPLETED, run.terminal_error
    assert run.final_opinion is not None


def _build_recoverable_controller(
    run_store, session_store, actions
) -> ResearchController:
    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([completed_result()]),
            )
        },
        store=run_store,
    )
    scientific = ScientificAgent(ScriptedLLMClient(actions), store=session_store)
    return ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=scientific,
        compiler=DeterministicWorkflowCompiler(proposal("work_1"), patch=None),
        scheduler=scheduler,
        registry=registry(),
    )


def test_real_restart_recovers_paused_scientific_session(tmp_path) -> None:
    run_store = JsonRunStore(tmp_path / "runs")
    session_store = JsonSessionStore(tmp_path / "sessions")
    ask_action = {
        "tool": "ask_user",
        "arguments": {"assessment": {"statement": "Need a user decision"}, 'text': 'Which dataset?', 'requested_fields': ['dataset']},
    }
    controller = _build_recoverable_controller(
        run_store, session_store, [ask_action, finish_action()]
    )
    paused = controller.create_run("run_restart", research_request())
    assert paused.status == RunStatus.PAUSED

    # Rebuild everything from disk: a fresh controller and a fresh Scientific
    # Agent that share the same persistent stores.
    rebuilt = _build_recoverable_controller(
        JsonRunStore(tmp_path / "runs"),
        JsonSessionStore(tmp_path / "sessions"),
        [finish_action()],
    )
    answer = UserAnswer(
        question_id=paused.pending_question.id,
        values={"dataset": "demo"},
        answered_at=NOW,
    )
    run = rebuilt.answer_question("run_restart", answer)

    assert run.status == RunStatus.COMPLETED, run.terminal_error
    assert run.final_opinion is not None
    restored = JsonRunStore(tmp_path / "runs").load(run.run_id)
    assert restored.answers[0].question_text == ask_action["arguments"]["text"]
    assert restored.answers[0].values == answer.values
    assert restored.scientific_session.id == paused.scientific_session.id
    context = rebuilt.scientific_port.llm_client.contexts[0]
    answer_ref = next(ref for ref in restored.artifacts.values() if ref.kind == "answer")
    assert _context_material(context, answer_ref) == restored.answers[0].model_dump(mode="json")


def test_restarted_generic_answers_stay_paired_with_their_own_questions(tmp_path):
    from resagent2_orchestrator import OrchestrationError

    def ask(text):
        return {"tool": "ask_user", "arguments": {"assessment": {"statement": "Need a user decision"}, 'text': text, 'requested_fields': ['answer']}}

    def rebuild(actions):
        return _build_recoverable_controller(
            JsonRunStore(tmp_path / "runs"), JsonSessionStore(tmp_path / "sessions"), actions,
        )

    first_text = "第一个选择 helper_a.py，第二个选择 helper_b.py。你选哪个？"
    second_text = "第一个模式是 add，第二个模式是 mul。你选哪个？"
    first = rebuild([ask(first_text)])
    paused = first.create_run("run_paired_answers", research_request())
    first_session = JsonSessionStore(tmp_path / "sessions").load(paused.scientific_session.id)
    # Even an in-process caller passing a subclass cannot supply the authoritative question.
    forged = RecordedAnswer(
        question_id=paused.pending_question.id, question_text="Forged question: choose helper_a.py",
        values={"answer": "第二个"}, answered_at=NOW, requested_fields=["answer"],
        run_id=paused.run_id, session_id=paused.scientific_session.id,
    )
    second = rebuild([ask(second_text)])
    again = second.answer_question(paused.run_id, forged)
    assert again.status == RunStatus.PAUSED
    assert again.answers[0].question_text == first_text
    assert again.pending_question.text == second_text
    assert again.pending_question.id != forged.question_id
    first_context = second.scientific_port.llm_client.contexts[0]
    first_ref = next(ref for ref in again.artifacts.values() if ref.kind == "answer")
    assert _context_material(first_context, first_ref) == again.answers[0].model_dump(mode="json")
    assert "Forged question" not in first_context.text

    before = JsonRunStore(tmp_path / "runs").load(paused.run_id).model_dump_json()
    with pytest.raises(OrchestrationError, match="does not match"):
        second.answer_question(paused.run_id, forged)
    assert JsonRunStore(tmp_path / "runs").load(paused.run_id).model_dump_json() == before

    third = rebuild([finish_action()])
    completed = third.answer_question(paused.run_id, UserAnswer(
        question_id=again.pending_question.id, values={"answer": "第二个"}, answered_at=NOW,
    ))
    assert completed.status == RunStatus.COMPLETED
    restored = JsonRunStore(tmp_path / "runs").load(paused.run_id)
    assert [answer.question_text for answer in restored.answers] == [first_text, second_text]
    assert [answer.values for answer in restored.answers] == [{"answer": "第二个"}] * 2
    last_context = third.scientific_port.llm_client.contexts[0]
    last_ref = next(ref for ref in restored.artifacts.values() if ref.kind == "answer" and ref.id != first_ref.id)
    assert _context_material(last_context, last_ref) == restored.answers[1].model_dump(mode="json")
    # Historical artifacts remain readable; only the new answer is a required resume material.
    assert f"material_{first_ref.id}" not in last_context.included_sections
    assert restored.delivered_answer_ids == [answer.question_id for answer in restored.answers]
    final_session = JsonSessionStore(tmp_path / "sessions").load(restored.scientific_session.id)
    assert final_session.session_id == first_session.session_id
    assert final_session.created_at == first_session.created_at
    assert len(final_session.events) > len(first_session.events)


def test_paired_answers_keep_task_and_scientific_scopes():
    controller = build_controller(actions=[request_work_action(), finish_action()])
    completed = controller.create_run("run_answer_scopes", research_request())
    task = completed.workflow.tasks[0]
    scopes = [
        {"session_id": completed.scientific_session.id},
        {"task_id": task.id, "attempt_number": 1},
        {"task_id": "task_other", "attempt_number": 1},
    ]
    answers = [RecordedAnswer(
        question_id=f"question_{name}", question_text=f"Question for {name}?",
        requested_fields=["answer"], run_id=completed.run_id,
        values={"answer": "second"}, answered_at=NOW, **scope,
    ) for name, scope in zip(("scientific", "experiment", "other"), scopes)]
    completed.answers = answers
    refs = [system_artifact(controller.scheduler.artifact_registry, completed, "answer", answer, **scope)
            for answer, scope in zip(answers, scopes)]
    request = controller.scheduler._module_request(completed, task, 1, parent_session_id="session_task_child")
    assert [read_json(ref, RecordedAnswer) for ref in request.input_artifacts if ref.kind == "answer"] == [answers[1]]
    assert request.resume_artifact_ids == [refs[1].id]
    assert controller._pending_answers(completed) == [answers[0]]
    scientific_request = controller._scientific_request(completed)
    assert scientific_request.resume_artifact_ids == [refs[0].id]
    assert refs[1] in scientific_request.input_artifacts
    assert refs[2] in scientific_request.input_artifacts
    from resagent2_components import RegisteredArtifactReader, read_request_material, ArtifactReadError
    reader = RegisteredArtifactReader(scientific_request.input_artifacts, run_id=completed.run_id)
    for ref, answer in zip(refs[1:], answers[1:]):
        assert json.loads(reader.read_text(ref.id)["content"])["question_text"] == answer.question_text
        with pytest.raises(ArtifactReadError, match="resumed invocation"):
            read_request_material(scientific_request, ref)
    completed.delivered_answer_ids = [answers[0].question_id]
    assert controller._pending_answers(completed) == []
    assert controller._scientific_request(completed).resume_artifact_ids == []
    assert controller.scheduler._module_request(completed, task, 1, parent_session_id="session_task_child").resume_artifact_ids == [refs[1].id]
    assert refs[2] not in request.input_artifacts


def test_budget_overrun_does_not_complete(tmp_path) -> None:
    """A final turn that pushes llm_calls past the budget must not complete."""
    tiny = ResearchRequest(goal='Evaluate', budget=RunBudget(max_llm_calls=1, timeout_seconds=60), permissions=RunPermissions(execute_commands=True, prepare_environment=True), execution_limits=ExecutionLimits(max_tasks=5, max_attempts_per_task=2))
    # First request_work consumes 1 LLM call; a second turn would overrun.
    actions = [request_work_action(), finish_action()]
    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    scientific = ScientificAgent(ScriptedLLMClient(actions), store=InMemorySessionStore())
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=scientific,
        compiler=DeterministicWorkflowCompiler(proposal("work_1"), patch=None),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.create_run("run_overrun", tiny)

    assert run.status == RunStatus.FAILED
    assert run.llm_calls_used >= tiny.budget.max_llm_calls


def test_compiling_restart_recompiles_without_workflow() -> None:
    """A crash after saving COMPILING but before accepting the workflow must not
    fail on a forbidden COMPILING -> COMPILING migration; the compiler is
    stateless, so the request is simply recompiled."""
    store = InMemoryRunStore()
    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([completed_result()]),
            )
        },
        store=store,
    )
    completed_session = SessionRef(
        id="session_sci",
        module=AgentOwner.SCIENTIFIC,
        state_uri="memory://session_sci",
        status=SessionStatus.COMPLETED,
        created_at=NOW,
        updated_at=NOW,
    )

    class _FinishingPort:
        def invoke(self, request):
            return AgentResult(status='completed', session=completed_session, llm_calls=1, report="Scientific conclusion", artifacts=[ArtifactCandidate(kind="scientific_opinion", path="opinion.json", media_type="application/json", summary="Conclusion", content=ScientificOpinion(verdict=ScientificVerdict.INCONCLUSIVE, statement='Execution completed without decisive evidence.').model_dump_json()), ArtifactCandidate(kind="observation_trace", path="observations.json", media_type="application/json", summary="Observed", content=json.dumps({"observed_artifact_ids": []}))])

    compiling = WorkRequest(
        id="work_1",
        run_id="run_compile_restart",
        scientific_session_id="session_sci",
        request=WorkRequestDraft(
            objective="Run the experiment",
            expected_evidence=["metric"],
        ),
        status=WorkRequestStatus.COMPILING,
        created_at=NOW,
        updated_at=NOW,
    )
    store.save(
        ResearchRun(
            run_id="run_compile_restart",
            request=research_request(),
            status=RunStatus.RUNNING,
            scientific_session=completed_session.model_copy(update={"status": SessionStatus.PAUSED}),
            work_requests=[compiling],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    recovered_state = store.load("run_compile_restart")
    recovered_state.conclusion_requirements_ref = system_artifact(
        scheduler.artifact_registry, recovered_state, "conclusion_requirements", ConclusionRequirements(),
    )
    store.save(recovered_state)
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=_FinishingPort(),
        compiler=DeterministicWorkflowCompiler(proposal("work_1"), patch=None),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.run_until_stable("run_compile_restart")

    assert run.status == RunStatus.COMPLETED, run.terminal_error


def test_compiler_llm_calls_enter_the_run_ledger() -> None:
    from resagent2_runtime.budget import current_budget
    class _CountingCompiler:
        def compile(self, request, *, current, registry, limits, workspaces=None):
            scope = current_budget()
            for index in range(7):
                scope.charge("test-compiler", index)
                scope.usage.complete("test-compiler", index, "succeeded")
            return CompilationResult(proposal(request.id), llm_calls=7)

    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=ScientificAgent(
            ScriptedLLMClient([request_work_action(), finish_action()]),
            store=InMemorySessionStore(),
        ),
        compiler=_CountingCompiler(),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.create_run("run_compiler_calls", research_request())

    assert run.status == RunStatus.COMPLETED, run.terminal_error
    # 7 compiler calls + 2 Scientific calls (request_work + finish).
    assert run.llm_calls_used == 9


@pytest.mark.parametrize("usage_known", [True, False])
def test_failed_compiler_usage_comes_from_reservations(tmp_path, usage_known) -> None:
    from resagent2_runtime.budget import current_budget
    class _FailingCompiler:
        # A stale implementation attribute must never override invocation usage.
        llm_calls = 99
        invocations = 0

        def compile(
            self, request, *, current, registry, limits,
            workspaces=None,
        ):
            self.invocations += 1
            if usage_known:
                for index in range(3):
                    current_budget().charge("failed-compiler", index)
                raise CompilationError("compiler failed", llm_calls=3)
            raise RuntimeError("compiler failed")

    compiler = _FailingCompiler()
    scheduler = WorkflowScheduler(
        bindings={},
        store=InMemoryRunStore(),
        artifact_root=tmp_path / "artifacts",
        data_root=tmp_path / "runs",
    )
    controller = ResearchController(
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=ScientificAgent(
            ScriptedLLMClient([request_work_action()]),
            store=InMemorySessionStore(),
        ),
        compiler=compiler,
        scheduler=scheduler,
        registry=registry(),
    )
    run = controller.create_run("run_compiler_usage_failure", research_request())

    assert compiler.invocations == 1
    assert run.status == RunStatus.FAILED
    assert run.work_requests[0].status.value == "failed"
    assert run.llm_calls_used == (4 if usage_known else 1)
    assert run.terminal_error is not None
    assert run.terminal_error.details["compiler_usage_known"] is usage_known


def test_required_outputs_are_frozen_in_the_conclusion_requirement_artifact():
    ask = {
        "tool": "ask_user",
        "arguments": {
            "assessment": {"statement": "Need the metric source"},
            "text": "Which source should supply the metrics?", "requested_fields": ["source"],
        },
    }
    controller = build_controller(actions=[ask])
    request = research_request().model_copy(update={"required_artifacts": ["metrics", "analysis"]})
    run = controller.create_run("run_frozen_outputs", request)
    assert run.status == RunStatus.PAUSED
    snapshot = run.conclusion_requirements_ref
    assert read_json(snapshot, ConclusionRequirements).required_artifacts == ["metrics", "analysis"]

    request.required_artifacts[:] = ["replacement"]
    persisted = controller.scheduler.store.load(run.run_id)
    assert persisted.conclusion_requirements_ref == snapshot
    assert read_json(persisted.conclusion_requirements_ref, ConclusionRequirements).required_artifacts == [
        "metrics", "analysis",
    ]
    forwarded = controller._scientific_request(persisted)
    assert snapshot in forwarded.input_artifacts


def test_controller_registers_required_scientific_candidate_before_final_acceptance():
    action = finish_action()
    action["arguments"]["artifacts"].append(ArtifactCandidate(
        kind="module_report", path="analysis.md", media_type="text/markdown",
        summary="Requested analysis", content="The analysis", output_name="analysis",
    ).model_dump(mode="json"))
    controller = build_controller(actions=[action])
    request = research_request().model_copy(update={"required_artifacts": ["analysis"]})
    run = controller.create_run("run_scientific_delivery", request)

    assert run.status == RunStatus.COMPLETED, run.terminal_error
    deliveries = [ref for ref in run.artifacts.values() if ref.output_name == "analysis"]
    assert len(deliveries) == 1
    assert deliveries[0].producer == AgentOwner.SCIENTIFIC
    assert deliveries[0].session_id == run.scientific_session.id
    assert run.final_opinion.evidence_artifact_ids == []
    assert run.scientific_observed_artifact_ids == []
    assert run.final_report_artifact_id in run.artifacts
    assert controller.scheduler.artifact_registry.missing_required_artifacts(
        ["analysis"], run_id=run.run_id, artifacts=run.artifacts,
    ) == []


def test_missing_output_feedback_can_execute_work_and_resume_without_citation():
    controller = build_controller(actions=[finish_action(), request_work_action(), finish_action()])
    produced = completed_result().model_copy(update={"artifacts": [
        ArtifactCandidate(
            kind="experiment_result", path="result.json", media_type="application/json",
            summary="Measured output", content='{"accuracy": 0.8}', output_name="metrics",
        ),
    ]})
    controller.scheduler.bindings[WorkflowAgentKind.EXPERIMENT] = ModuleBinding(
        owner=AgentOwner.EXPERIMENT, port=ScriptedModulePort([produced]),
    )
    request = research_request().model_copy(update={"required_artifacts": ["metrics"]})
    run = controller.create_run("run_required_work", request)

    assert run.status == RunStatus.COMPLETED, run.terminal_error
    assert len(run.work_requests) == 1
    assert run.work_requests[0].status == WorkRequestStatus.CONSUMED
    assert run.work_requests[0].scientific_session_id == run.scientific_session.id
    assert any(ref.output_name == "metrics" for ref in run.artifacts.values())
    assert run.final_opinion.evidence_artifact_ids == []
    assert run.scientific_observed_artifact_ids == []
    contexts = controller.scientific_port.llm_client.contexts
    assert len(contexts) == 3
    assert "required_artifact_missing" in contexts[1].text
    assert "subject=metrics" in contexts[1].text
    persisted = controller.scientific_port.store.load(run.scientific_session.id)
    assert persisted.session_id == run.scientific_session.id
    assert persisted.llm_calls_used == run.llm_calls_used == 3
