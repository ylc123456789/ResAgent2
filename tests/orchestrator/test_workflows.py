from datetime import UTC, datetime

from resagent2_contracts import (
    AgentOwner,
    Capability,
    CodeModifyInput,
    ExperimentRunInput,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    QuestionDraft,
    RunBudget,
    RunStatus,
    ScientificAnalyzeInput,
    ScientificPlanInput,
    SessionRef,
    SessionStatus,
    SuccessCriterion,
    TaskProposal,
    TaskStatus,
    UserAnswer,
    VerificationMode,
    WorkflowPatch,
    WorkflowProposal,
    ResearchRequest,
    ErrorCode,
)
from resagent2_orchestrator import (
    InMemoryRunStore,
    ModuleBinding,
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


def criterion() -> SuccessCriterion:
    return SuccessCriterion(
        description="The fake module returns a valid result",
        verification=VerificationMode.AUTOMATIC,
        evidence_key="result",
    )


def task(task_id: str, capability: Capability, depends_on=()) -> TaskProposal:
    if capability == Capability.SCIENTIFIC_PLAN:
        inputs = ScientificPlanInput(request=research_request())
    elif capability == Capability.SCIENTIFIC_ANALYZE:
        inputs = ScientificAnalyzeInput(
            question="What does the evidence show?",
            evidence_artifact_ids=[],
        )
    elif capability == Capability.CODE_MODIFY:
        inputs = CodeModifyInput(instructions="Apply the required repair")
    else:
        inputs = ExperimentRunInput(instructions=f"Run {task_id}")
    return TaskProposal(
        id=task_id,
        capability=capability,
        goal=f"Complete {task_id}",
        rationale="Required by the test workflow",
        depends_on=list(depends_on),
        inputs=inputs,
        success_criteria=[criterion()],
    )


def completed(summary="done") -> ModuleResult:
    return ModuleResult(status=ModuleStatus.COMPLETED, summary=summary, payload={})


def scheduler(scripts: dict[Capability, list[ModuleResult]]) -> WorkflowScheduler:
    owners = {
        Capability.SCIENTIFIC_PLAN: AgentOwner.SCIENTIFIC,
        Capability.SCIENTIFIC_ANALYZE: AgentOwner.SCIENTIFIC,
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


def test_linear_workflow_runs_to_completion() -> None:
    workflow = WorkflowProposal(
        summary="linear",
        scientific_rationale="A minimal research sequence",
        tasks=[
            task("task_plan", Capability.SCIENTIFIC_PLAN),
            task("task_code", Capability.CODE_MODIFY, ["task_plan"]),
            task("task_experiment", Capability.EXPERIMENT_RUN, ["task_code"]),
            task("task_analyze", Capability.SCIENTIFIC_ANALYZE, ["task_experiment"]),
        ],
    )
    engine = scheduler(
        {
            Capability.SCIENTIFIC_PLAN: [completed()],
            Capability.CODE_MODIFY: [completed()],
            Capability.EXPERIMENT_RUN: [completed()],
            Capability.SCIENTIFIC_ANALYZE: [completed()],
        }
    )

    engine.create_run("run_linear", research_request(), workflow)
    result = engine.run_until_stable("run_linear")

    assert result.status == RunStatus.COMPLETED
    assert [item.status for item in result.workflow.tasks] == [
        TaskStatus.COMPLETED,
        TaskStatus.COMPLETED,
        TaskStatus.COMPLETED,
        TaskStatus.COMPLETED,
    ]
    assert [item.attempts[0].number for item in result.workflow.tasks] == [1, 1, 1, 1]


def test_parallel_ready_set_is_stable_and_dependency_driven() -> None:
    proposal = WorkflowProposal(
        summary="parallel",
        scientific_rationale="Compare two runs",
        tasks=[
            task("task_plan", Capability.SCIENTIFIC_PLAN),
            task("task_baseline", Capability.EXPERIMENT_RUN, ["task_plan"]),
            task("task_treatment", Capability.EXPERIMENT_RUN, ["task_plan"]),
            task(
                "task_analyze",
                Capability.SCIENTIFIC_ANALYZE,
                ["task_baseline", "task_treatment"],
            ),
        ],
    )
    engine = scheduler(
        {
            Capability.SCIENTIFIC_PLAN: [completed()],
            Capability.EXPERIMENT_RUN: [completed("baseline"), completed("treatment")],
            Capability.SCIENTIFIC_ANALYZE: [completed()],
        }
    )
    engine.create_run("run_parallel", research_request(), proposal)

    assert engine.ready_task_ids("run_parallel") == ["task_plan"]
    engine.execute_task("run_parallel", "task_plan")
    assert engine.ready_task_ids("run_parallel") == [
        "task_baseline",
        "task_treatment",
    ]
    assert engine.ready_task_ids("run_parallel") == [
        "task_baseline",
        "task_treatment",
    ]


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
        summary="repair",
        scientific_rationale="Exercise explicit recovery",
        tasks=[task("task_experiment", Capability.EXPERIMENT_RUN)],
    )
    engine.create_run("run_repair", research_request(), proposal)

    first = engine.run_until_stable("run_repair")
    assert first.status == RunStatus.FAILED
    assert first.workflow.tasks[0].status == TaskStatus.BLOCKED

    patched = engine.apply_patch(
        "run_repair",
        WorkflowPatch(
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
    assert final.status == RunStatus.COMPLETED
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
            module=AgentOwner.SCIENTIFIC,
            state_uri="memory://child",
            status=SessionStatus.PAUSED,
            created_at=NOW,
            updated_at=NOW,
        ),
    )
    port = ScriptedModulePort([question_result, completed()])
    engine = WorkflowScheduler(
        bindings={
            Capability.SCIENTIFIC_PLAN: ModuleBinding(
                owner=AgentOwner.SCIENTIFIC,
                port=port,
            )
        },
        store=InMemoryRunStore(),
    )
    proposal = WorkflowProposal(
        summary="question",
        scientific_rationale="Ask for missing input",
        tasks=[task("task_plan", Capability.SCIENTIFIC_PLAN)],
    )
    engine.create_run("run_question", research_request(), proposal)

    paused = engine.run_until_stable("run_question")
    assert paused.status == RunStatus.PAUSED
    assert paused.pending_question is not None
    answer = UserAnswer(
        question_id=paused.pending_question.id,
        values={"dataset": "demo"},
        answered_at=NOW,
    )
    engine.answer_question("run_question", answer)
    final = engine.run_until_stable("run_question")

    assert final.status == RunStatus.COMPLETED
    assert port.requests[1].answers == [answer]
    assert port.requests[1].parent_session_id == "session_child"


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
    engine.create_run(
        "run_failed_payload",
        research_request(),
        WorkflowProposal(
            summary="failure",
            scientific_rationale="Status is machine-owned",
            tasks=[task("task_experiment", Capability.EXPERIMENT_RUN)],
        ),
    )

    run = engine.run_until_stable("run_failed_payload")

    assert run.status == RunStatus.FAILED
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
    engine.create_run(
        "run_auto_retry",
        research_request(),
        WorkflowProposal(
            summary="retry",
            scientific_rationale="Retry a transient failure",
            tasks=[task("task_experiment", Capability.EXPERIMENT_RUN)],
        ),
    )

    run = engine.run_until_stable("run_auto_retry")

    assert run.status == RunStatus.COMPLETED
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
    engine.create_run(
        "run_invalid_port",
        research_request(),
        WorkflowProposal(
            summary="invalid port",
            scientific_rationale="Validate the module boundary",
            tasks=[task("task_experiment", Capability.EXPERIMENT_RUN)],
        ),
    )

    run = engine.run_until_stable("run_invalid_port")

    error = run.workflow.tasks[0].attempts[0].error
    assert run.status == RunStatus.FAILED
    assert error is not None
    assert error.code == ErrorCode.CONTRACT_ERROR
