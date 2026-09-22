"""Deterministic tests for the multi-round repair flow (scenario 3).

Verifies two things:
- the compiler rejects a patch that supersedes/updates tasks from a previous
  work request (they are immutable history);
- a failed experiment followed by a new WorkRequest that adds repair tasks
  preserves the failed attempt and completes.
"""

from __future__ import annotations

from resagent2_contracts import RunPermissions, ExecutionLimits

import json
import pytest

from resagent2_contracts import (
    AgentOwner,
    AgentResult,
    ArtifactCandidate,
    ErrorCode,
    ModuleError,
    ModuleStatus,
    ResearchRequest,
    RunBudget,
    RunStatus,
    ScientificVerdict,
    TaskProposal,
    TaskStatus,
    WorkflowAgentDefinition,
    WorkflowAgentKind,
    WorkflowAgentRegistry,
    WorkflowPatch,
    WorkflowProposal,
)
from resagent2_orchestrator import (
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


def _registry() -> WorkflowAgentRegistry:
    return WorkflowAgentRegistry(
        definitions=[
            WorkflowAgentDefinition(
                workflow_agent_kind=WorkflowAgentKind.CODING,

            ),
            WorkflowAgentDefinition(
                workflow_agent_kind=WorkflowAgentKind.EXPERIMENT,

            ),
        ]
    )


def _completed() -> AgentResult:
    return AgentResult(status=ModuleStatus.COMPLETED, report="done")


def _failed() -> AgentResult:
    return AgentResult(
        status=ModuleStatus.FAILED,
        report="boom",
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
    return {"tool": "finish", "arguments": {"report": "Scientific conclusion", "artifacts": [ArtifactCandidate(kind="scientific_opinion", path="scientific_opinion.json", media_type="application/json", summary="Scientific conclusion", content=json.dumps({'verdict': ScientificVerdict.INCONCLUSIVE.value, 'statement': 'the first run failed, then a fix restored it', 'limitations': ['the first run failed before the fix']})).model_dump(mode="json")]}}


def test_candidate_rejects_empty_graph() -> None:
    from resagent2_orchestrator.workflow_validation import validate_workflow_candidate

    proposal = WorkflowProposal(
        work_request_id="work_1",
        tasks=[],
    )
    with pytest.raises(ValueError, match="empty task graph"):
        validate_workflow_candidate(proposal)


def test_candidate_rejects_cross_request_dependency() -> None:
    with pytest.raises(ValueError, match="unknown task"):
        WorkflowPatch(
            work_request_id="work_2",
            based_on_revision=1,
            add_tasks=[TaskProposal(
                id="task_exp2", work_request_id="work_2",
                workflow_agent_kind=WorkflowAgentKind.EXPERIMENT,
                instruction="Rerun",
                depends_on=["task_exp"],  # a prior work request's failed task
            )],
        )


def test_repair_flow_preserves_failed_task_and_completes(tmp_path) -> None:
    # Round 1: a single experiment task that fails.
    proposal = WorkflowProposal(
        work_request_id="work_1",
        tasks=[
            TaskProposal(
                id="task_exp",
                work_request_id="work_1",
                workflow_agent_kind=WorkflowAgentKind.EXPERIMENT,
                instruction="Run the experiment",

            )
        ],
    )
    # Round 2: a repair that only ADDS a fix + a rerun (no supersede/update).
    patch = WorkflowPatch(
        work_request_id="work_2",
        based_on_revision=1,
        add_tasks=[
            TaskProposal(
                id="task_fix",
                work_request_id="work_2",
                workflow_agent_kind=WorkflowAgentKind.CODING,
                instruction="Fix the bug",

            ),
            TaskProposal(
                id="task_exp2",
                work_request_id="work_2",
                workflow_agent_kind=WorkflowAgentKind.EXPERIMENT,
                instruction="Rerun the experiment",

            ),
        ],
    )

    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.CODING: ModuleBinding(
                owner=AgentOwner.CODING,
                port=ScriptedModulePort([_completed()]),
            ),
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
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
    request = ResearchRequest(goal='Run the experiment; if it fails, fix and rerun.', budget=RunBudget(max_llm_calls=50, timeout_seconds=60), permissions=RunPermissions(execute_commands=True, prepare_environment=True), execution_limits=ExecutionLimits(max_tasks=5, max_attempts_per_task=3))

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
        if not self._drafts:
            raise AssertionError("no more scripted drafts")
        return self._drafts.pop(0)


def _proposal_draft() -> dict:
    return {
        "tasks": [
            {'key': 'run_initial', "workflow_agent_kind": 'experiment', 'instruction': 'Run the experiment'}
        ],
    }


def _repair_draft() -> dict:
    return {
        "tasks": [
            {'key': 'fix', "workflow_agent_kind": 'coding', 'instruction': 'Fix the bug'},
            {'key': 'rerun', "workflow_agent_kind": 'experiment', 'instruction': 'Rerun the experiment', 'depends_on': ['fix']},
        ],
    }


def _finish_after_repair() -> dict:
    return {"tool": "finish", "arguments": {"report": "Scientific conclusion", "artifacts": [ArtifactCandidate(kind="scientific_opinion", path="scientific_opinion.json", media_type="application/json", summary="Scientific conclusion", content=json.dumps({'verdict': ScientificVerdict.INCONCLUSIVE.value, 'statement': 'the first run failed, then a fix restored it', 'limitations': ['the first run failed before the fix']})).model_dump(mode="json")]}}


def test_repair_flow_with_semantic_compiler(tmp_path) -> None:
    """The production LLMWorkflowCompiler drives the repair loop end to end.

    Round 1 compiles a semantic draft into a proposal (one experiment task that
    fails); round 2 compiles a repair draft into an append-only patch that adds
    a fix + rerun. The failed attempt is preserved as immutable history.
    """
    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.CODING: ModuleBinding(
                owner=AgentOwner.CODING,
                port=ScriptedModulePort([_completed()]),
            ),
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
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
    request = ResearchRequest(goal='Run the experiment; if it fails, fix and rerun.', budget=RunBudget(max_llm_calls=50, timeout_seconds=60), permissions=RunPermissions(execute_commands=True, prepare_environment=True), execution_limits=ExecutionLimits(max_tasks=5, max_attempts_per_task=3))

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
