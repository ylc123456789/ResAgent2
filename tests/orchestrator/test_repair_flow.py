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


def test_compiler_rejects_cross_request_supersede() -> None:
    patch = WorkflowPatch(
        work_request_id="work_2",
        based_on_revision=1,
        reason="repair",
        supersede_task_ids=["task_exp"],
    )
    from resagent2_orchestrator.compiler import _reject_cross_request_mutations

    with pytest.raises(CompilationError, match="only add tasks"):
        _reject_cross_request_mutations(patch)


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
                rationale="evidence",
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
                rationale="repair",
                inputs=CodeModifyInput(instructions="Fix the bug"),
            ),
            TaskProposal(
                id="task_exp2",
                work_request_id="work_2",
                capability=Capability.EXPERIMENT_RUN,
                goal="Rerun the experiment",
                rationale="re-obtain evidence",
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
