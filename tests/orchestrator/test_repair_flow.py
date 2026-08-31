"""Deterministic tests for the multi-round repair flow (scenario 3).

Verifies two things:
- the compiler rejects a patch that supersedes/updates tasks from a previous
  work request (they are immutable history);
- a failed experiment followed by a new WorkRequest that adds repair tasks
  preserves the failed attempt and completes.
"""

from __future__ import annotations

import pytest

from resagent2_contracts import (
    AgentOwner,
    Capability,
    CapabilityDefinition,
    CapabilityRegistry,
    CodeModifyInput,
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
    TaskStatus,
    WorkflowPatch,
    WorkflowProposal,
)
from resagent2_orchestrator import (
    CompilationError,
    DeterministicWorkflowCompiler,
    InMemoryRunStore,
    LLMWorkflowCompiler,
    ModuleBinding,
    ResearchController,
    ScriptedModulePort,
    WorkflowScheduler,
)
from resagent2_runtime import InMemorySessionStore, ScriptedLLMClient
from resagent2_scientific import ScientificAgent


def _registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        definitions=[
            CapabilityDefinition(
                capability=Capability.CODE_MODIFY,
                owner=AgentOwner.CODING,
                request_model="CodeModifyInput",
                result_model="CodeModifyResult",
                permission_policy="read_write_workspace",
                completion_evidence=["code_change"],
            ),
            CapabilityDefinition(
                capability=Capability.EXPERIMENT_RUN,
                owner=AgentOwner.EXPERIMENT,
                request_model="ExperimentRunInput",
                result_model="ExperimentResult",
                permission_policy="read_write_workspace",
                completion_evidence=["experiment_result"],
            ),
        ]
    )


def _completed() -> ModuleResult:
    return ModuleResult(status=ModuleStatus.COMPLETED, summary="done", payload={})


def _failed() -> ModuleResult:
    return ModuleResult(
        status=ModuleStatus.FAILED,
        summary="boom",
        error=ModuleError(code=ErrorCode.TOOL_FAILED, message="boom", retryable=False),
    )


def _request_work() -> dict:
    return {
        "tool": "request_work",
        "arguments": {
            "assessment": {"statement": "need evidence"},
            "work_request": {
                "objective": "Produce the accuracy evidence",
                "expected_evidence": ["accuracy"],
            },
        },
    }


def _finish() -> dict:
    return {
        "tool": "finish",
        "arguments": {
            "opinion": {
                "verdict": ScientificVerdict.INCONCLUSIVE.value,
                "statement": "the first run failed, then a fix restored it",
                "acknowledged_task_ids": ["task_exp"],
                "limitations": ["the first run failed before the fix"],
            },
            "summary": "done",
        },
    }


def test_compiler_rejects_empty_graph() -> None:
    from resagent2_orchestrator.compiler import _reject_empty_graph

    proposal = WorkflowProposal(
        work_request_id="work_1",
        summary="empty",
        compilation_rationale="no work",
        tasks=[],
    )
    with pytest.raises(CompilationError, match="empty task graph"):
        _reject_empty_graph(proposal)


def test_compiler_rejects_cross_request_dependency() -> None:
    from resagent2_orchestrator.compiler import _reject_cross_request_dependencies

    patch = WorkflowPatch(
        work_request_id="work_2",
        based_on_revision=1,
        reason="repair",
        add_tasks=[
            TaskProposal(
                id="task_exp2",
                work_request_id="work_2",
                capability=Capability.EXPERIMENT_RUN,
                goal="Rerun",
                depends_on=["task_exp"],  # a prior work request's failed task
                inputs=ExperimentRunInput(instructions="Rerun"),
            )
        ],
    )
    with pytest.raises(CompilationError, match="outside the patch"):
        _reject_cross_request_dependencies(patch)


def test_repair_flow_preserves_failed_task_and_completes(tmp_path) -> None:
    # Round 1: a single experiment task that fails.
    proposal = WorkflowProposal(
        work_request_id="work_1",
        summary="run the experiment",
        compilation_rationale="first attempt",
        tasks=[
            TaskProposal(
                id="task_exp",
                work_request_id="work_1",
                capability=Capability.EXPERIMENT_RUN,
                goal="Run the experiment",
                inputs=ExperimentRunInput(instructions="Run the experiment"),
            )
        ],
    )
    # Round 2: a repair that only ADDS a fix + a rerun (no supersede/update).
    patch = WorkflowPatch(
        work_request_id="work_2",
        based_on_revision=1,
        reason="repair the failed experiment",
        add_tasks=[
            TaskProposal(
                id="task_fix",
                work_request_id="work_2",
                capability=Capability.CODE_MODIFY,
                goal="Fix the bug",
                inputs=CodeModifyInput(instructions="Fix the bug"),
            ),
            TaskProposal(
                id="task_exp2",
                work_request_id="work_2",
                capability=Capability.EXPERIMENT_RUN,
                goal="Rerun the experiment",
                inputs=ExperimentRunInput(instructions="Rerun"),
            ),
        ],
    )

    scheduler = WorkflowScheduler(
        bindings={
            Capability.CODE_MODIFY: ModuleBinding(
                owner=AgentOwner.CODING,
                port=ScriptedModulePort([_completed()]),
            ),
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([_failed(), _completed()]),
            ),
        },
        store=InMemoryRunStore(),
        artifact_root=tmp_path / "artifacts",
    )
    scientific = ScientificAgent(
        ScriptedLLMClient([_request_work(), _request_work(), _finish()]),
        store=InMemorySessionStore(),
    )
    controller = ResearchController(
        scientific_port=scientific,
        compiler=DeterministicWorkflowCompiler(proposal, patch),
        scheduler=scheduler,
        registry=_registry(),
    )
    request = ResearchRequest(
        goal="Run the experiment; if it fails, fix and rerun.",
        budget=RunBudget(
            max_tasks=5, max_attempts_per_task=3, max_llm_calls=50, timeout_seconds=60
        ),
    )

    run = controller.create_run("run_repair", request)

    assert run.status == RunStatus.COMPLETED, [v.message for v in run.completion_violations]
    tasks = {task.id: task for task in run.workflow.tasks}
    # The old failed experiment is preserved as history, not overwritten.
    assert tasks["task_exp"].status == TaskStatus.FAILED
    assert tasks["task_exp"].attempts and tasks["task_exp"].attempts[-1].status.value == "failed"
    # The repair tasks were added and completed.
    assert tasks["task_fix"].status == TaskStatus.COMPLETED
    assert tasks["task_exp2"].status == TaskStatus.COMPLETED
    # Two work requests: the initial run and the repair.
    assert len(run.work_requests) >= 2


class _ScriptedCompilerLLM:
    """Return scripted drafts and auto-accept semantic reviews."""

    def __init__(self, drafts: list[dict]) -> None:
        self._drafts = list(drafts)

    def next_action(self, prompt, action_type):
        from resagent2_orchestrator.compiler import CompilationReview

        if action_type is CompilationReview:
            return {"accepted": True}
        if not self._drafts:
            raise AssertionError("no more scripted drafts")
        return self._drafts.pop(0)


def _proposal_draft() -> dict:
    return {
        "summary": "run the experiment",
        "rationale": "first attempt",
        "tasks": [
            {
                "key": "run_initial",
                "capability": "experiment_run",
                "goal": "Run the experiment",
                "inputs": {"capability": "experiment_run", "instructions": "Run the experiment"},
            }
        ],
    }


def _repair_draft() -> dict:
    return {
        "summary": "repair",
        "rationale": "fix the bug and rerun",
        "tasks": [
            {
                "key": "fix",
                "capability": "code_modify",
                "goal": "Fix the bug",
                "inputs": {"capability": "code_modify", "instructions": "Fix the bug"},
            },
            {
                "key": "rerun",
                "capability": "experiment_run",
                "goal": "Rerun the experiment",
                "depends_on": ["fix"],
                "inputs": {"capability": "experiment_run", "instructions": "Rerun"},
            },
        ],
    }


def _finish_after_repair() -> dict:
    return {
        "tool": "finish",
        "arguments": {
            "opinion": {
                "verdict": ScientificVerdict.INCONCLUSIVE.value,
                "statement": "the first run failed, then a fix restored it",
                "acknowledged_task_ids": ["task_run_initial"],
                "limitations": ["the first run failed before the fix"],
            },
            "summary": "done",
        },
    }


def test_repair_flow_with_semantic_compiler(tmp_path) -> None:
    """The production LLMWorkflowCompiler drives the repair loop end to end.

    Round 1 compiles a semantic draft into a proposal (one experiment task that
    fails); round 2 compiles a repair draft into an append-only patch that adds
    a fix + rerun. The failed attempt is preserved as immutable history.
    """
    scheduler = WorkflowScheduler(
        bindings={
            Capability.CODE_MODIFY: ModuleBinding(
                owner=AgentOwner.CODING,
                port=ScriptedModulePort([_completed()]),
            ),
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([_failed(), _completed()]),
            ),
        },
        store=InMemoryRunStore(),
        artifact_root=tmp_path / "artifacts",
    )
    scientific = ScientificAgent(
        ScriptedLLMClient([_request_work(), _request_work(), _finish_after_repair()]),
        store=InMemorySessionStore(),
    )
    controller = ResearchController(
        scientific_port=scientific,
        compiler=LLMWorkflowCompiler(
            _ScriptedCompilerLLM([_proposal_draft(), _repair_draft()])
        ),
        scheduler=scheduler,
        registry=_registry(),
    )
    request = ResearchRequest(
        goal="Run the experiment; if it fails, fix and rerun.",
        budget=RunBudget(
            max_tasks=5, max_attempts_per_task=3, max_llm_calls=50, timeout_seconds=60
        ),
    )

    run = controller.create_run("run_repair_semantic", request)

    assert run.status == RunStatus.COMPLETED, [v.message for v in run.completion_violations]
    tasks = {task.id: task for task in run.workflow.tasks}
    # The old failed experiment is preserved, not overwritten.
    assert tasks["task_run_initial"].status == TaskStatus.FAILED
    assert tasks["task_run_initial"].attempts[-1].status.value == "failed"
    # The repair tasks were added and completed (with code-assigned ids).
    assert tasks["task_fix"].status == TaskStatus.COMPLETED
    assert tasks["task_rerun"].status == TaskStatus.COMPLETED
    assert tasks["task_rerun"].depends_on == ["task_fix"]
    assert len(run.work_requests) >= 2
