"""Tests for the Phase 7.5 ResearchController (DEVELOPMENT_PLAN §7.5)."""

from datetime import UTC, datetime

import pytest

from resagent2_contracts import (
    AgentOwner,
    ArtifactRef,
    Capability,
    CapabilityDefinition,
    CapabilityRegistry,
    ErrorCode,
    ExperimentRunInput,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    ResearchRequest,
    RunBudget,
    RunStatus,
    ScientificVerdict,
    TaskProposal,
    UserAnswer,
    WorkflowPatch,
    WorkflowProposal,
)
from resagent2_orchestrator import (
    DeterministicWorkflowCompiler,
    InMemoryRunStore,
    JsonRunStore,
    ModuleBinding,
    ResearchController,
    ScriptedModulePort,
    WorkflowScheduler,
)
from resagent2_runtime import InMemorySessionStore, ScriptedLLMClient
from resagent2_scientific import ScientificAgent

NOW = datetime(2026, 8, 28, tzinfo=UTC)


def research_request() -> ResearchRequest:
    return ResearchRequest(
        goal="Evaluate the method",
        budget=RunBudget(
            max_tasks=5,
            max_attempts_per_task=2,
            max_llm_calls=50,
            timeout_seconds=60,
        ),
    )


def registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        definitions=[
            CapabilityDefinition(
                capability=Capability.EXPERIMENT_RUN,
                owner=AgentOwner.EXPERIMENT,
                request_model="ExperimentRunInput",
                result_model="ExperimentResult",
                permission_policy="read_write_workspace",
                completion_evidence=["experiment_result"],
            )
        ]
    )


def proposal(work_request_id: str) -> WorkflowProposal:
    return WorkflowProposal(
        work_request_id=work_request_id,
        summary="run one experiment",
        compilation_rationale="produce evidence",
        tasks=[
            TaskProposal(
                id="task_experiment",
                work_request_id=work_request_id,
                capability=Capability.EXPERIMENT_RUN,
                goal="Run the experiment",
                rationale="produce metrics",
                inputs=ExperimentRunInput(instructions="Run once"),
            )
        ],
    )


def completed_result() -> ModuleResult:
    return ModuleResult(status=ModuleStatus.COMPLETED, summary="done", payload={})


def build_controller(
    *,
    actions: list[dict],
    store=None,
) -> ResearchController:
    scheduler = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
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
        scientific_port=scientific,
        compiler=compiler,
        scheduler=scheduler,
        registry=registry(),
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


def finish_action(verdict=ScientificVerdict.INCONCLUSIVE, acknowledged=()) -> dict:
    opinion = {"verdict": verdict.value, "statement": "done"}
    if acknowledged:
        opinion["acknowledged_task_ids"] = list(acknowledged)
        opinion["limitations"] = ["a task failed"]
    return {
        "tool": "finish",
        "arguments": {
            "opinion": opinion,
            "summary": "complete",
        },
    }


def test_direct_conclusion_without_work() -> None:
    controller = build_controller(actions=[finish_action(ScientificVerdict.INCONCLUSIVE)])

    run = controller.create_run("run_direct", research_request())

    assert run.status == RunStatus.COMPLETED
    assert run.final_opinion is not None
    assert run.work_requests == []


def test_single_work_cycle_completes() -> None:
    controller = build_controller(
        actions=[request_work_action(), finish_action()]
    )

    run = controller.create_run("run_cycle", research_request())

    assert run.status == RunStatus.COMPLETED
    assert run.final_opinion is not None
    assert len(run.work_requests) == 1
    assert run.work_requests[0].status.value == "consumed"


def _cycle_compiler():
    """A compiler that emits a proposal for the first request and a patch (adding
    a fresh task) for every subsequent request on the same workflow."""

    class _CycleCompiler:
        def compile(self, request, *, current, registry, budget):
            if current is None:
                return proposal(request.id)
            # A new request on an existing workflow becomes a patch adding one task.
            return WorkflowPatch(
                work_request_id=request.id,
                based_on_revision=current.revision,
                reason="request alternative work",
                add_tasks=[
                    TaskProposal(
                        id=f"task_{request.id}",
                        work_request_id=request.id,
                        capability=Capability.EXPERIMENT_RUN,
                        goal=f"Run for {request.id}",
                        rationale="produce evidence",
                        inputs=ExperimentRunInput(instructions="Run once"),
                    )
                ],
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
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([completed_result(), completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    scientific = ScientificAgent(ScriptedLLMClient(actions), store=InMemorySessionStore())
    controller = ResearchController(
        scientific_port=scientific,
        compiler=_cycle_compiler(),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.create_run("run_multi", research_request())

    assert run.status == RunStatus.COMPLETED
    assert len(run.work_requests) == 2
    assert [item.status.value for item in run.work_requests] == ["consumed", "consumed"]


def test_task_failure_then_request_alternative_work(tmp_path) -> None:
    failed = ModuleResult(
        status=ModuleStatus.FAILED,
        summary="crashed",
        error=ModuleError(
            code=ErrorCode.TOOL_FAILED,
            message="crashed",
            retryable=False,
        ),
    )
    scheduler = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([failed, completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    actions = [
        request_work_action(),
        request_work_action(),
        finish_action(acknowledged=["task_experiment"]),
    ]
    scientific = ScientificAgent(ScriptedLLMClient(actions), store=InMemorySessionStore())
    controller = ResearchController(
        scientific_port=scientific,
        compiler=_cycle_compiler(),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.create_run("run_failure", research_request())

    assert run.status == RunStatus.COMPLETED
    assert len(run.work_requests) == 2


def test_paused_question_then_answer_resumes() -> None:
    ask_action = {
        "tool": "ask_user",
        "arguments": {
            "assessment": {"statement": "need dataset"},
            "text": "Which dataset?",
            "requested_fields": ["dataset"],
            "reason": "missing",
        },
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

    assert run.status == RunStatus.COMPLETED


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
        def compile(self, request, *, current, registry, budget):
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
        scientific_port=scientific,
        compiler=_FailingCompiler(),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.create_run("run_compile_fail", research_request())

    assert run.status == RunStatus.FAILED
    assert run.work_requests[0].status.value == "failed"


def test_second_work_outcome_contains_only_second_round_tasks() -> None:
    actions = [
        request_work_action(),
        request_work_action(),
        finish_action(),
    ]
    scheduler = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([completed_result(), completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    scientific = ScientificAgent(ScriptedLLMClient(actions), store=InMemorySessionStore())
    controller = ResearchController(
        scientific_port=scientific,
        compiler=_cycle_compiler(),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.create_run("run_outcome_isolate", research_request())

    assert run.status == RunStatus.COMPLETED
    first = run.work_requests[0]
    second = run.work_requests[1]
    assert first.outcome.work_request_id == first.id
    assert [t.task_id for t in first.outcome.tasks] == ["task_experiment"]
    assert second.outcome.work_request_id == second.id
    assert [t.task_id for t in second.outcome.tasks] == ["task_work_2"]


def test_failed_task_appears_in_unresolved_then_acknowledged() -> None:
    failed = ModuleResult(
        status=ModuleStatus.FAILED,
        summary="crashed",
        error=ModuleError(
            code=ErrorCode.TOOL_FAILED, message="crashed", retryable=False
        ),
    )
    scheduler = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([failed, completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    actions = [
        request_work_action(),
        request_work_action(),
        finish_action(acknowledged=["task_experiment"]),
    ]
    scientific = ScientificAgent(ScriptedLLMClient(actions), store=InMemorySessionStore())
    controller = ResearchController(
        scientific_port=scientific,
        compiler=_cycle_compiler(),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.create_run("run_unresolved", research_request())

    assert run.status == RunStatus.COMPLETED
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

        def run(self, request):
            result = self.agent.run(request)
            # Forge an extra observed id into the result.
            return result.model_copy(
                update={"observed_artifact_ids": ["artifact_fake"]}
            )

    scheduler = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    controller = ResearchController(
        scientific_port=_ForgingPort(),
        compiler=DeterministicWorkflowCompiler(proposal("work_1"), patch=None),
        scheduler=scheduler,
        registry=registry(),
    )

    run = controller.create_run("run_forged", research_request())

    assert run.status == RunStatus.COMPLETED
    assert run.scientific_observed_artifact_ids == []


def test_run_total_llm_budget_exhaustion() -> None:
    # A tiny run budget so the accumulated Scientific turns exceed it.
    tiny = ResearchRequest(
        goal="Evaluate",
        budget=RunBudget(
            max_tasks=5,
            max_attempts_per_task=2,
            max_llm_calls=1,
            timeout_seconds=60,
        ),
    )
    actions = [request_work_action(), request_work_action()]
    scheduler = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([completed_result(), completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    scientific = ScientificAgent(ScriptedLLMClient(actions), store=InMemorySessionStore())
    controller = ResearchController(
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
        "arguments": {
            "assessment": {"statement": "need dataset"},
            "text": "Which dataset?",
            "requested_fields": ["dataset"],
            "reason": "missing",
        },
    }
    actions = [ask_action, request_work_action(), finish_action()]
    scheduler = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([completed_result()]),
            )
        },
        store=InMemoryRunStore(),
    )
    scientific = ScientificAgent(ScriptedLLMClient(actions), store=InMemorySessionStore())
    controller = ResearchController(
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

    assert run.status == RunStatus.COMPLETED
    assert run.final_opinion is not None
