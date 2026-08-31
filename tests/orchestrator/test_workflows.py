from datetime import UTC, datetime

import pytest

from resagent2_contracts import (
    AgentOwner,
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
    required: bool = True,
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
        rationale="Required by the test workflow",
        depends_on=list(depends_on),
        required=required,
        inputs=inputs,
    )


def completed(summary="done") -> ModuleResult:
    return ModuleResult(status=ModuleStatus.COMPLETED, summary=summary, payload={})


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
            Capability.CODE_MODIFY: [completed()],
            Capability.EXPERIMENT_RUN: [completed()],
            Capability.CODE_UNDERSTAND: [completed()],
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
            Capability.CODE_UNDERSTAND: [completed()],
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
            Capability.CODE_MODIFY: [completed("repair completed")],
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
    engine.store.save(run)
    engine.resume_task("run_question", "task_experiment")
    final = engine.run_until_stable("run_question")

    assert final.workflow.tasks[0].status == TaskStatus.COMPLETED
    # The same Attempt resumed, not a new one.
    assert [attempt.number for attempt in final.workflow.tasks[0].attempts] == [1]
    assert port.requests[1].answers == [answer]
    assert port.requests[1].parent_session_id == "session_child"
    assert port.requests[1].attempt_number == 1


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


def test_patch_cannot_supersede_task_from_another_work_request() -> None:
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
    with pytest.raises(OrchestrationError, match="another work request"):
        engine.apply_patch(
            "run_isolate",
            WorkflowPatch(
                work_request_id="work_b",
                based_on_revision=1,
                reason="cross-request supersede",
                supersede_task_ids=["task_a"],
            ),
        )
