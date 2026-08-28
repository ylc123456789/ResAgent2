"""Deterministic mock golden loop for the Phase 7 scientific control path.

Runs the full closed loop with no real LLM, no real legacy modules and no
network:

    ResearchRequest -> ScientificAgent(request_work) -> WorkflowCompiler
    -> WorkflowScheduler(code + experiment) -> WorkOutcome
    -> ScientificAgent(finish) -> ScientificCompletionValidator -> completed

The Scientific Agent first asks for evidence, the deterministic compiler turns
that into a code -> experiment graph, the scheduler executes both with scripted
ports, and the resumed Scientific Agent concludes. The run persists to disk so
a fresh store can prove recovery.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    Capability,
    CapabilityDefinition,
    CapabilityRegistry,
    CodeModifyInput,
    ExperimentRunInput,
    ModuleResult,
    ModuleStatus,
    ResearchRequest,
    RunBudget,
    RunStatus,
    ScientificVerdict,
    TaskProposal,
    WorkflowProposal,
)
from resagent2_orchestrator import (
    DeterministicWorkflowCompiler,
    JsonRunStore,
    ModuleBinding,
    ResearchController,
    ScriptedModulePort,
    WorkflowScheduler,
)
from resagent2_runtime import InMemorySessionStore, ScriptedLLMClient
from resagent2_scientific import ScientificAgent

RUN_ID = "run_golden"
WORK_REQUEST_ID = "work_1"


def registry() -> CapabilityRegistry:
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


def proposal() -> WorkflowProposal:
    return WorkflowProposal(
        work_request_id=WORK_REQUEST_ID,
        summary="produce evidence for the scientific conclusion",
        compilation_rationale="golden code -> experiment loop",
        tasks=[
            TaskProposal(
                id="task_code",
                work_request_id=WORK_REQUEST_ID,
                capability=Capability.CODE_MODIFY,
                goal="Prepare the method implementation",
                rationale="Produce a verified code change",
                inputs=CodeModifyInput(instructions="Prepare the method"),
            ),
            TaskProposal(
                id="task_experiment",
                work_request_id=WORK_REQUEST_ID,
                capability=Capability.EXPERIMENT_RUN,
                goal="Run the experiment and record metrics",
                rationale="Produce evidence for the conclusion",
                depends_on=["task_code"],
                inputs=ExperimentRunInput(
                    instructions="Run the experiment and record metrics",
                    expected_metrics=["accuracy"],
                ),
            ),
        ],
    )


def completed_result() -> ModuleResult:
    return ModuleResult(status=ModuleStatus.COMPLETED, summary="done", payload={})


def request_work_action() -> dict:
    return {
        "tool": "request_work",
        "arguments": {
            "assessment": {"statement": "need evidence"},
            "work_request": {
                "objective": "Produce evidence for the method",
                "expected_evidence": ["accuracy"],
            },
        },
    }


def finish_action() -> dict:
    return {
        "tool": "finish",
        "arguments": {
            "opinion": {"verdict": ScientificVerdict.INCONCLUSIVE.value, "statement": "done"},
            "summary": "complete",
        },
    }


def run_mock_e2e(*, workdir: Path | None = None):
    """Run the golden loop once and return the completed ResearchRun."""
    workdir = workdir or Path(tempfile.mkdtemp(prefix="resagent2-e2e-"))

    scheduler = WorkflowScheduler(
        bindings={
            Capability.CODE_MODIFY: ModuleBinding(
                owner=AgentOwner.CODING,
                port=ScriptedModulePort([completed_result()]),
            ),
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([completed_result()]),
            ),
        },
        store=JsonRunStore(workdir / "state"),
        artifact_root=workdir / "artifacts",
    )
    scientific = ScientificAgent(
        ScriptedLLMClient([request_work_action(), finish_action()]),
        store=InMemorySessionStore(),
    )
    controller = ResearchController(
        scientific_port=scientific,
        compiler=DeterministicWorkflowCompiler(proposal(), patch=None),
        scheduler=scheduler,
        registry=registry(),
    )

    request = ResearchRequest(
        goal="Determine whether the method improves accuracy",
        budget=RunBudget(
            max_tasks=5,
            max_attempts_per_task=3,
            max_llm_calls=50,
            timeout_seconds=60,
        ),
    )
    return controller.create_run(RUN_ID, request)


def _summarize(run) -> str:
    lines = [f"run={run.run_id} status={run.status.value} artifacts={len(run.artifacts)}"]
    lines.append(f"opinion={run.final_opinion.statement if run.final_opinion else None}")
    lines.append(f"report={run.final_report_artifact_id}")
    for task in run.workflow.tasks:
        attempts = ", ".join(f"{a.number}:{a.status.value}" for a in task.attempts)
        lines.append(f"  {task.id} [{task.capability.value}] attempts={attempts}")
    return "\n".join(lines)


def main() -> None:
    run = run_mock_e2e()
    assert run.status == RunStatus.COMPLETED, f"golden loop did not complete: {run.status}"
    print(_summarize(run))


if __name__ == "__main__":
    main()
