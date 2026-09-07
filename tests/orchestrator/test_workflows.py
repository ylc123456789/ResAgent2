from datetime import UTC, datetime


import pytest

from resagent2_contracts import (
    AgentOwner,
    Attempt,
    AttemptStatus,
    Capability,
    CodeModifyInput,
    CodeUnderstandInput,
    ExperimentRunInput,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    QuestionDraft,
    RunBudget,
    RunStatus,
    SessionRef,
    SessionStatus,
    TaskProposal,
    TaskStatus,
    UserAnswer,
    WorkflowPatch,
    WorkflowProposal,
    ResearchRequest,
    ErrorCode,
)
from resagent2_orchestrator import (
    InMemoryRunStore,
    ModuleBinding,
    OrchestrationError,
    ResearchRun,
    ScriptedModulePort,
    WorkflowScheduler,
)


NOW = datetime(2026, 8, 26, tzinfo=UTC)


def research_request() -> ResearchRequest:
    return ResearchRequest(
        goal="Evaluate a method",
        budget=RunBudget(
            max_tasks=12,
            max_attempts_per_task=3,
            max_llm_calls=50,
            timeout_seconds=3600,
        ),
    )


def task(
    task_id: str,
    capability: Capability,
    depends_on=(),
    *,
    work_request_id: str = "work_legacy_initial",
) -> TaskProposal:
    if capability == Capability.CODE_UNDERSTAND:
        inputs = CodeUnderstandInput(question="What does the evidence show?")
    elif capability == Capability.CODE_MODIFY:
        inputs = CodeModifyInput(instructions="Apply the required repair")
    else:
        inputs = ExperimentRunInput(instructions=f"Run {task_id}")
    return TaskProposal(
        id=task_id,
        work_request_id=work_request_id,
        capability=capability,
        goal=f"Complete {task_id}",
        depends_on=list(depends_on),
        inputs=inputs,
    )


def completed(summary="done", *, capability=Capability.EXPERIMENT_RUN) -> ModuleResult:
    if capability == Capability.CODE_UNDERSTAND:
        payload = {"answer": "Code inspected", "evidence_files": ["train.py"]}
    elif capability == Capability.CODE_MODIFY:
        payload = {
            "changed_files": ["train.py"], "patch_path": "changes.patch",
            "verification_passed": True, "verification_results": [{
                "command": "python -m pytest", "exit_code": 0,
                "stdout_path": "verify.stdout", "stderr_path": "verify.stderr",
                "duration_seconds": 0.0,
            }],
        }
    else:
        payload = {"env_id": "resenv_test"}
    return ModuleResult(status=ModuleStatus.COMPLETED, summary=summary, payload=payload)


def scheduler(scripts: dict[Capability, list[ModuleResult]]) -> WorkflowScheduler:
    owners = {
        Capability.CODE_UNDERSTAND: AgentOwner.CODING,
        Capability.CODE_MODIFY: AgentOwner.CODING,
        Capability.EXPERIMENT_RUN: AgentOwner.EXPERIMENT,
    }
    bindings = {
        capability: ModuleBinding(
            owner=owners[capability],
            port=ScriptedModulePort(results),
        )
        for capability, results in scripts.items()
    }
    return WorkflowScheduler(bindings=bindings, store=InMemoryRunStore())


def _create_run(engine, run_id, request, proposal):
    now = datetime.now(UTC)
    engine.store.save(
        ResearchRun(
            run_id=run_id,
            request=request,
            status=RunStatus.RUNNING,
            created_at=now,
            updated_at=now,
        )
    )
    return engine.accept_proposal(run_id, proposal)


def _ask_result() -> ModuleResult:
    """A paused needs_user_input result, for question-flow tests."""
    return ModuleResult(
        status=ModuleStatus.NEEDS_USER_INPUT,
        summary="input required",
        question=QuestionDraft(
            text="pick one", requested_fields=["x"], reason="no input was given"
        ),
        session=SessionRef(
            id="session_child",
            module=AgentOwner.EXPERIMENT,
            state_uri="memory://child",
            status=SessionStatus.PAUSED,
            created_at=NOW,
            updated_at=NOW,
        ),
    )


def _pause_with_question(task_id: str, run_id: str) -> ResearchRun:
    engine = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([_ask_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    proposal = WorkflowProposal(
        work_request_id="work_legacy_initial",
        summary="question",
        compilation_rationale="ask for input",
        tasks=[task(task_id, Capability.EXPERIMENT_RUN)],
    )
    _create_run(engine, run_id, research_request(), proposal)
    return engine.run_until_stable(run_id)


def test_linear_workflow_runs_to_completion() -> None:
    workflow = WorkflowProposal(
        work_request_id="work_legacy_initial",
        summary="linear",
        compilation_rationale="A minimal research sequence",
        tasks=[
            task("task_code", Capability.CODE_MODIFY),
            task("task_experiment", Capability.EXPERIMENT_RUN, ["task_code"]),
            task("task_analyze", Capability.CODE_UNDERSTAND, ["task_experiment"]),
        ],
    )
    engine = scheduler(
        {
            Capability.CODE_MODIFY: [completed(capability=Capability.CODE_MODIFY)],
            Capability.EXPERIMENT_RUN: [completed()],
            Capability.CODE_UNDERSTAND: [completed(capability=Capability.CODE_UNDERSTAND)],
        }
    )

    _create_run(engine, "run_linear", research_request(), workflow)
    result = engine.run_until_stable("run_linear")

    assert result.status == RunStatus.RUNNING  # the scheduler never completes a run
    assert [item.status for item in result.workflow.tasks] == [
        TaskStatus.COMPLETED,
        TaskStatus.COMPLETED,
        TaskStatus.COMPLETED,
    ]
    assert [item.attempts[0].number for item in result.workflow.tasks] == [1, 1, 1]


def test_parallel_ready_set_is_stable_and_dependency_driven() -> None:
    proposal = WorkflowProposal(
        work_request_id="work_legacy_initial",
        summary="parallel",
        compilation_rationale="Compare two runs",
        tasks=[
            task("task_baseline", Capability.EXPERIMENT_RUN),
            task("task_treatment", Capability.EXPERIMENT_RUN),
            task(
                "task_analyze",
                Capability.CODE_UNDERSTAND,
                ["task_baseline", "task_treatment"],
            ),
        ],
    )
    engine = scheduler(
        {
            Capability.EXPERIMENT_RUN: [completed("baseline"), completed("treatment")],
            Capability.CODE_UNDERSTAND: [completed(capability=Capability.CODE_UNDERSTAND)],
        }
    )
    _create_run(engine, "run_parallel", research_request(), proposal)

    assert engine.ready_task_ids("run_parallel") == [
        "task_baseline",
        "task_treatment",
    ]
    assert engine.ready_task_ids("run_parallel") == [
        "task_baseline",
        "task_treatment",
    ]
    engine.execute_task("run_parallel", "task_baseline")
    assert engine.ready_task_ids("run_parallel") == ["task_treatment"]


def test_blocked_experiment_can_be_repaired_without_overwriting_attempts() -> None:
    blocked = ModuleResult(
        status=ModuleStatus.BLOCKED,
        summary="Code repair required",
        error=ModuleError(
            code=ErrorCode.TOOL_FAILED,
            message="Experiment cannot start",
            retryable=False,
        ),
    )
    engine = scheduler(
        {
            Capability.EXPERIMENT_RUN: [blocked, completed("retry succeeded")],
            Capability.CODE_MODIFY: [completed("repair completed", capability=Capability.CODE_MODIFY)],
        }
    )
    proposal = WorkflowProposal(
        work_request_id="work_legacy_initial",
        summary="repair",
        compilation_rationale="Exercise explicit recovery",
        tasks=[task("task_experiment", Capability.EXPERIMENT_RUN)],
    )
    _create_run(engine, "run_repair", research_request(), proposal)

    first = engine.run_until_stable("run_repair")
    assert first.workflow.tasks[0].status == TaskStatus.BLOCKED

    patched = engine.apply_patch(
        "run_repair",
        WorkflowPatch(
            work_request_id="work_legacy_initial",
            based_on_revision=1,
            reason="Add an explicit repair task",
            add_tasks=[task("task_repair", Capability.CODE_MODIFY)],
        ),
    )
    assert [item.revision for item in patched.workflow_history] == [1]
    engine.run_until_stable("run_repair")
    engine.retry_task("run_repair", "task_experiment")
    final = engine.run_until_stable("run_repair")

    experiment = next(item for item in final.workflow.tasks if item.id == "task_experiment")
    assert experiment.status == TaskStatus.COMPLETED
    assert [attempt.number for attempt in experiment.attempts] == [1, 2]
    assert experiment.attempts[0].status.value == "blocked"
    assert experiment.attempts[1].status.value == "completed"


def test_question_pauses_and_answer_resumes_same_task_context() -> None:
    question_result = ModuleResult(
        status=ModuleStatus.NEEDS_USER_INPUT,
        summary="Dataset required",
        question=QuestionDraft(
            text="Which dataset?",
            requested_fields=["dataset"],
            reason="No dataset was selected",
        ),
        session=SessionRef(
            id="session_child",
            module=AgentOwner.EXPERIMENT,
            state_uri="memory://child",
            status=SessionStatus.PAUSED,
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    port = ScriptedModulePort([question_result, completed()])
    engine = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=port,
            )
        },
        store=InMemoryRunStore(),
    )
    proposal = WorkflowProposal(
        work_request_id="work_legacy_initial",
        summary="question",
        compilation_rationale="Ask for missing input",
        tasks=[task("task_experiment", Capability.EXPERIMENT_RUN)],
    )
    _create_run(engine, "run_question", research_request(), proposal)

    paused = engine.run_until_stable("run_question")
    assert paused.status == RunStatus.PAUSED
    assert paused.pending_question is not None
    # The paused Attempt is non-terminal: no finished_at, and it resumes on the
    # same Attempt number (ADR-0011 §2).
    attempt = paused.workflow.tasks[0].attempts[0]
    assert attempt.status.value == "needs_user_input"
    assert attempt.finished_at is None

    answer = UserAnswer(
        question_id=paused.pending_question.id,
        values={"dataset": "demo"},
        answered_at=NOW,
    )
    # The controller owns run-level answer state; the scheduler only moves the
    # task back into the ready set.
    run = engine.store.load("run_question")
    run.answers.append(answer)
    run.pending_question = None
    run.status = RunStatus.RUNNING
    run.answer_task_ids[answer.question_id] = "task_experiment"
    engine.resume_task_in_place(run, "task_experiment")
    engine.store.save(run)
    final = engine.run_until_stable("run_question")

    assert final.workflow.tasks[0].status == TaskStatus.COMPLETED
    # The same Attempt resumed, not a new one.
    assert [attempt.number for attempt in final.workflow.tasks[0].attempts] == [1]
    assert port.requests[1].answers == [answer]
    assert port.requests[1].parent_session_id == "session_child"
    assert port.requests[1].attempt_number == 1


def test_long_task_id_question_stays_within_id_cap() -> None:
    paused = _pause_with_question("task_" + "a" * 128, "run_long_question")

    assert paused.status == RunStatus.PAUSED
    assert paused.pending_question is not None
    assert len(paused.pending_question.id) <= 137
    assert paused.pending_question.id.startswith("question_")


def test_question_id_keeps_short_task_ids_readable() -> None:
    paused = _pause_with_question("task_experiment", "run_short_question")

    assert paused.pending_question is not None
    assert paused.pending_question.id.startswith("question_experiment_1_")


def test_successive_questions_in_one_attempt_reject_the_previous_answer() -> None:
    from resagent2_orchestrator.scheduler import _validate_answer

    port = ScriptedModulePort([_ask_result(), _ask_result()])
    engine = WorkflowScheduler(
        bindings={Capability.EXPERIMENT_RUN: ModuleBinding(
            owner=AgentOwner.EXPERIMENT, port=port,
        )},
        store=InMemoryRunStore(),
    )
    proposal = WorkflowProposal(
        work_request_id="work_legacy_initial", summary="ask twice",
        compilation_rationale="Two user choices in one execution attempt",
        tasks=[task("task_experiment", Capability.EXPERIMENT_RUN)],
    )
    _create_run(engine, "run_two_questions", research_request(), proposal)
    first = engine.run_until_stable("run_two_questions")
    answer = UserAnswer(
        question_id=first.pending_question.id, values={"x": "first"},
        answered_at=NOW,
    )
    run = engine.store.load(first.run_id)
    run.answers.append(answer)
    run.answer_task_ids[answer.question_id] = "task_experiment"
    run.pending_question = None
    run.status = RunStatus.RUNNING
    engine.resume_task_in_place(run, "task_experiment")
    engine.store.save(run)

    # A new scheduler reads the persisted answer/Attempt; no in-memory counter.
    restarted = WorkflowScheduler(bindings=engine.bindings, store=engine.store)
    second = restarted.run_until_stable(run.run_id)
    assert second.pending_question.id != answer.question_id
    assert len(second.workflow.tasks[0].attempts) == 1
    assert port.requests[1].attempt_number == 1
    assert port.requests[1].parent_session_id == "session_child"
    with pytest.raises(OrchestrationError, match="does not match"):
        _validate_answer(second.pending_question, answer)
    _validate_answer(second.pending_question, UserAnswer(
        question_id=second.pending_question.id, values={"x": "second"},
        answered_at=NOW,
    ))


@pytest.mark.parametrize(
    ("task_id", "attempt_number"),
    [
        ("task_" + "a" * 128, 1),
        ("task_" + "a" * 128, 10**30),
        ("task_" + "a" * 128, 10**128),
    ],
)
def test_question_id_is_strictly_bounded(task_id: str, attempt_number: int) -> None:
    from pydantic import TypeAdapter

    from resagent2_contracts import QuestionId
    from resagent2_orchestrator.scheduler import _question_id

    qid = _question_id(task_id, attempt_number)
    assert TypeAdapter(QuestionId).validate_python(qid) == qid


def test_failed_payload_cannot_be_promoted_to_completed() -> None:
    failed = ModuleResult(
        status=ModuleStatus.FAILED,
        summary="Native failure must win over payload text",
        payload={"status": "completed", "summary": "looks successful"},
        error=ModuleError(
            code=ErrorCode.TOOL_FAILED,
            message="Actual execution failed",
            retryable=False,
        ),
    )
    engine = scheduler({Capability.EXPERIMENT_RUN: [failed]})
    _create_run(engine,
        "run_failed_payload",
        research_request(),
        WorkflowProposal(
            work_request_id="work_legacy_initial",
            summary="failure",
            compilation_rationale="Status is machine-owned",
            tasks=[task("task_experiment", Capability.EXPERIMENT_RUN)],
        ),
    )

    run = engine.run_until_stable("run_failed_payload")

    assert run.workflow.tasks[0].status == TaskStatus.FAILED


def test_retryable_failure_creates_a_new_attempt_automatically() -> None:
    retryable = ModuleResult(
        status=ModuleStatus.FAILED,
        summary="temporary failure",
        error=ModuleError(
            code=ErrorCode.TIMEOUT,
            message="temporary timeout",
            retryable=True,
        ),
    )
    engine = scheduler(
        {Capability.EXPERIMENT_RUN: [retryable, completed("retry worked")]}
    )
    _create_run(engine,
        "run_auto_retry",
        research_request(),
        WorkflowProposal(
            work_request_id="work_legacy_initial",
            summary="retry",
            compilation_rationale="Retry a transient failure",
            tasks=[task("task_experiment", Capability.EXPERIMENT_RUN)],
        ),
    )

    run = engine.run_until_stable("run_auto_retry")

    assert run.workflow.tasks[0].status == TaskStatus.COMPLETED
    assert [item.number for item in run.workflow.tasks[0].attempts] == [1, 2]


def test_recovery_closes_interrupted_attempt_and_uses_normal_retry_budget() -> None:
    engine = scheduler({Capability.EXPERIMENT_RUN: [completed("retry worked")]})
    _create_run(engine, "run_interrupted", research_request(), WorkflowProposal(
        work_request_id="work_legacy_initial",
        summary="interrupted",
        compilation_rationale="recover one persisted attempt",
        tasks=[task("task_experiment", Capability.EXPERIMENT_RUN)],
    ))
    run = engine.store.load("run_interrupted")
    workflow_task = run.workflow.tasks[0]
    workflow_task.status = TaskStatus.RUNNING
    workflow_task.attempts.append(
        Attempt(
            number=1,
            status=AttemptStatus.RUNNING,
            started_at=NOW,
        )
    )
    engine.store.save(run)

    recovered = engine.store.load("run_interrupted")
    assert engine._recover_interrupted_attempts_in_place(recovered)
    engine.store.save(recovered)
    interrupted = recovered.workflow.tasks[0].attempts[0]
    assert interrupted.status == AttemptStatus.FAILED
    assert interrupted.error is not None
    assert interrupted.error.code == ErrorCode.INTERRUPTED
    assert recovered.workflow.tasks[0].status == TaskStatus.PENDING

    final = engine.run_until_stable("run_interrupted")
    assert final.workflow.tasks[0].status == TaskStatus.COMPLETED
    assert [item.number for item in final.workflow.tasks[0].attempts] == [1, 2]


def test_budget_exhaustion_does_not_persist_a_running_attempt() -> None:
    engine = scheduler({Capability.EXPERIMENT_RUN: [completed()]})
    _create_run(engine, "run_no_calls", research_request(), WorkflowProposal(
        work_request_id="work_legacy_initial",
        summary="no budget",
        compilation_rationale="reject before starting",
        tasks=[task("task_experiment", Capability.EXPERIMENT_RUN)],
    ))
    run = engine.store.load("run_no_calls")
    run.llm_calls_used = run.request.budget.max_llm_calls
    engine.store.save(run)

    with pytest.raises(OrchestrationError, match="LLM-call budget"):
        engine.execute_task("run_no_calls", "task_experiment")

    unchanged = engine.store.load("run_no_calls").workflow.tasks[0]
    assert unchanged.status == TaskStatus.PENDING
    assert unchanged.attempts == []


def test_task_request_work_is_a_contract_failure_not_a_question() -> None:
    request_work = ModuleResult(
        status=ModuleStatus.REQUEST_WORK,
        summary="invalid task result",
        request_work={},
        session=SessionRef(
            id="session_invalid_task",
            module=AgentOwner.EXPERIMENT,
            state_uri="session://session_invalid_task",
            status=SessionStatus.PAUSED,
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    engine = scheduler({Capability.EXPERIMENT_RUN: [request_work]})
    _create_run(engine, "run_invalid_request_work", research_request(), WorkflowProposal(
        work_request_id="work_legacy_initial",
        summary="invalid control signal",
        compilation_rationale="task modules cannot request workflow work",
        tasks=[task("task_experiment", Capability.EXPERIMENT_RUN)],
    ))

    run = engine.run_until_stable("run_invalid_request_work")
    attempt = run.workflow.tasks[0].attempts[0]
    assert run.pending_question is None
    assert attempt.status == AttemptStatus.FAILED
    assert attempt.error is not None
    assert attempt.error.code == ErrorCode.CONTRACT_ERROR


def test_invalid_module_port_result_becomes_contract_failure() -> None:
    class InvalidPort:
        def invoke(self, request):
            return {"status": "completed"}

    engine = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=InvalidPort(),
            )
        },
        store=InMemoryRunStore(),
    )
    _create_run(engine,
        "run_invalid_port",
        research_request(),
        WorkflowProposal(
            work_request_id="work_legacy_initial",
            summary="invalid port",
            compilation_rationale="Validate the module boundary",
            tasks=[task("task_experiment", Capability.EXPERIMENT_RUN)],
        ),
    )

    run = engine.run_until_stable("run_invalid_port")

    error = run.workflow.tasks[0].attempts[0].error
    assert run.workflow.tasks[0].status == TaskStatus.FAILED
    assert error is not None
    assert error.code == ErrorCode.CONTRACT_ERROR


def test_ready_work_keeps_run_running_until_it_is_executed() -> None:
    engine = scheduler({Capability.EXPERIMENT_RUN: [completed()]})
    created = _create_run(engine,
        "run_ready_gate",
        research_request(),
        WorkflowProposal(
            work_request_id="work_legacy_initial",
            summary="ready gate",
            compilation_rationale="Ready work prevents early completion",
            tasks=[task("task_experiment", Capability.EXPERIMENT_RUN)],
        ),
    )

    assert created.status == RunStatus.RUNNING
    assert engine.ready_task_ids("run_ready_gate") == ["task_experiment"]
    assert created.workflow.tasks[0].attempts == []


def test_patch_is_append_only() -> None:
    engine = scheduler({Capability.EXPERIMENT_RUN: [completed()]})
    _create_run(engine,
        "run_isolate",
        research_request(),
        WorkflowProposal(
            work_request_id="work_a",
            summary="first work request",
            compilation_rationale="initial",
            tasks=[task("task_a", Capability.EXPERIMENT_RUN, work_request_id="work_a")],
        ),
    )
    patched = engine.apply_patch(
        "run_isolate",
        WorkflowPatch(
            work_request_id="work_b",
            based_on_revision=1,
            reason="append a follow-up task",
            add_tasks=[task("task_b", Capability.EXPERIMENT_RUN, work_request_id="work_b")],
        ),
    )
    assert [t.id for t in patched.workflow.tasks] == ["task_a", "task_b"]


@pytest.mark.parametrize("kind", ["proposal", "patch"])
def test_empty_candidate_is_rejected_without_mutation(tmp_path, kind) -> None:
    engine = WorkflowScheduler(
        bindings={}, store=InMemoryRunStore(),
        artifact_root=tmp_path / "artifacts", data_root=tmp_path / "data",
    )
    now = datetime.now(UTC)
    run = ResearchRun(
        run_id="run_candidate", request=research_request(), status=RunStatus.RUNNING,
        created_at=now, updated_at=now,
    )
    if kind == "patch":
        from resagent2_contracts import Workflow
        run.workflow = Workflow(
            run_id=run.run_id, revision=1, tasks=[],
            created_from="work_legacy_initial",
        )
    engine.store.save(run)
    before = engine.store.load(run.run_id).model_dump()
    with pytest.raises(OrchestrationError, match="empty task graph"):
        if kind == "proposal":
            engine.accept_proposal(run.run_id, WorkflowProposal(
                work_request_id="work_legacy_initial", summary="empty",
                compilation_rationale="invalid replacement compiler", tasks=[],
            ))
        else:
            engine.apply_patch(run.run_id, WorkflowPatch(
                work_request_id="work_next", based_on_revision=1, reason="empty",
                add_tasks=[],
            ))
    assert engine.store.load(run.run_id).model_dump() == before
    assert not engine.run_layout.run_dir(run.run_id).exists()


@pytest.mark.parametrize("old_status", [TaskStatus.COMPLETED, TaskStatus.FAILED])
def test_patch_rejects_prior_round_dependency_without_mutation(tmp_path, old_status) -> None:
    engine = WorkflowScheduler(
        bindings={Capability.EXPERIMENT_RUN: ModuleBinding(
            owner=AgentOwner.EXPERIMENT, port=ScriptedModulePort([]),
        )},
        store=InMemoryRunStore(), artifact_root=tmp_path / "artifacts",
        data_root=tmp_path / "data",
    )
    run = _create_run(engine, "run_candidate", research_request(), WorkflowProposal(
        work_request_id="work_legacy_initial", summary="first",
        compilation_rationale="initial round",
        tasks=[task("task_old", Capability.EXPERIMENT_RUN)],
    ))
    run.workflow.tasks[0].status = old_status
    engine.store.save(run)
    before = engine.store.load(run.run_id).model_dump()
    with pytest.raises(OrchestrationError, match="outside the current work request"):
        engine.apply_patch(run.run_id, WorkflowPatch(
            work_request_id="work_next", based_on_revision=1, reason="invalid",
            add_tasks=[task(
                "task_new", Capability.EXPERIMENT_RUN, ["task_old"],
                work_request_id="work_next",
            )],
        ))
    assert engine.store.load(run.run_id).model_dump() == before

    patched = engine.apply_patch(run.run_id, WorkflowPatch(
        work_request_id="work_next", based_on_revision=1, reason="independent next round",
        add_tasks=[
            task("task_fix", Capability.EXPERIMENT_RUN, work_request_id="work_next"),
            task("task_rerun", Capability.EXPERIMENT_RUN, ["task_fix"], work_request_id="work_next"),
        ],
    ))
    assert patched.workflow.revision == 2
    assert patched.workflow.tasks[0].status == old_status
    assert patched.workflow.tasks[-1].depends_on == ["task_fix"]
