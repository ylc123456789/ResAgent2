"""Deterministic scientific -> execution -> evidence -> conclusion loop."""

from resagent2_contracts import RunPermissions, ExecutionLimits

import json
import tempfile
from pathlib import Path
from resagent2_contracts import (
    AgentOwner, AgentResult, ArtifactCandidate, ModuleStatus, ResearchRequest,
    RunBudget, RunStatus, TaskAcceptanceSpec, TaskProposal, WorkflowAgentKind,
    WorkflowAgentDefinition, WorkflowAgentRegistry, WorkflowProposal,
)
from resagent2_orchestrator import (
    DeterministicWorkflowCompiler, JsonRunStore, ModuleBinding, ResearchController,
    ScriptedModulePort, WorkflowScheduler,
)
from resagent2_runtime import InMemorySessionStore, ScriptedLLMClient
from resagent2_scientific import ScientificAgent

RUN_ID = "run_golden"
WORK_REQUEST_ID = "work_1"


def registry():
    return WorkflowAgentRegistry(definitions=[WorkflowAgentDefinition(workflow_agent_kind=kind) for kind in WorkflowAgentKind])


def proposal():
    return WorkflowProposal(work_request_id=WORK_REQUEST_ID, tasks=[
        TaskProposal(id="task_code", work_request_id=WORK_REQUEST_ID, workflow_agent_kind="coding", instruction="Prepare the method implementation"),
        TaskProposal(id="task_experiment", work_request_id=WORK_REQUEST_ID, workflow_agent_kind="experiment",
                     instruction="Run the comparison and provide accuracy", depends_on=["task_code"],
                     acceptance_spec=TaskAcceptanceSpec(required_metric_keys=["accuracy"])),
    ])


def completed_result(kind):
    artifacts = []
    if kind == WorkflowAgentKind.EXPERIMENT:
        artifacts.append(ArtifactCandidate(kind="metrics", path="metrics.json", media_type="application/json",
                                           summary="Measured accuracy", content='{"accuracy": 0.9}'))
    return AgentResult(status=ModuleStatus.COMPLETED, report="done", artifacts=artifacts)


def request_work_action():
    return {"tool": "request_work", "arguments": {"assessment": {"statement": "need evidence"},
            "work_request": {"objective": "Produce evidence for the method", "expected_evidence": ["accuracy"]}}}


def finish_action():
    return {"tool": "finish", "arguments": {"report": "done", "artifacts": [{
        "kind": "scientific_opinion", "path": "opinion.json", "media_type": "application/json",
        "summary": "Scientific conclusion", "content": json.dumps({"verdict": "inconclusive", "statement": "done",
        "evidence_artifact_ids": ["artifact_experiment_1_1"], "limitations": ["Deterministic fixture only"]}),
    }]}}


def run_mock_e2e(*, workdir: Path | None = None):
    workdir = workdir or Path(tempfile.mkdtemp(prefix="resagent2-e2e-"))
    scheduler = WorkflowScheduler(
        bindings={kind: ModuleBinding(owner=AgentOwner(kind.value), port=ScriptedModulePort([completed_result(kind)])) for kind in WorkflowAgentKind},
        store=JsonRunStore(workdir / "state"), artifact_root=workdir / "artifacts", data_root=workdir,
    )
    scientific = ScientificAgent(ScriptedLLMClient([
        request_work_action(),
        {"tool": "read_artifact", "arguments": {"artifact_id": "artifact_experiment_1_1"}},
        finish_action(),
    ]), store=InMemorySessionStore())
    controller = ResearchController(scientific_port=scientific, compiler=DeterministicWorkflowCompiler(proposal()),
                                    scheduler=scheduler, registry=registry())
    return controller.create_run(RUN_ID, ResearchRequest(goal='Determine whether the method improves accuracy', budget=RunBudget(max_llm_calls=50, timeout_seconds=60), permissions=RunPermissions(execute_commands=True, prepare_environment=True), execution_limits=ExecutionLimits(max_tasks=5, max_attempts_per_task=3)))


def _summarize(run):
    lines = [f"run={run.run_id} status={run.status.value} artifacts={len(run.artifacts)}",
             f"opinion={run.final_opinion.statement if run.final_opinion else None}", f"report={run.final_report_artifact_id}"]
    for task in run.workflow.tasks if run.workflow else []:
        attempts = ", ".join(f"{a.number}:{a.status.value}" for a in task.attempts)
        lines.append(f"  {task.id} [{task.workflow_agent_kind.value}] attempts={attempts}")
    if run.terminal_error:
        lines.append(run.terminal_error.message)
    return "\n".join(lines)


def main():
    run = run_mock_e2e()
    print(_summarize(run))
    assert run.status == RunStatus.COMPLETED


if __name__ == "__main__":
    main()
