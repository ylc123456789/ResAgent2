from datetime import UTC, datetime

from resagent2_contracts import (
    AgentOwner,
    Capability,
    ExperimentRunInput,
    ModuleResult,
    ModuleStatus,
    ResearchRequest,
    RunBudget,
    RunStatus,
    TaskProposal,
    WorkflowProposal,
)
from resagent2_orchestrator import (
    InMemoryRunStore,
    ModuleBinding,
    ResearchRun,
    ScriptedModulePort,
    WorkflowScheduler,
)


def request() -> ResearchRequest:
    return ResearchRequest(
        goal="Record a payload",
        budget=RunBudget(
            max_tasks=5,
            max_attempts_per_task=2,
            max_llm_calls=10,
            timeout_seconds=60,
        ),
    )


def proposal() -> WorkflowProposal:
    return WorkflowProposal(
        work_request_id="work_legacy_initial",
        summary="one task",
        compilation_rationale="Payload persistence test",
        tasks=[
            TaskProposal(
                id="task_experiment",
                work_request_id="work_legacy_initial",
                capability=Capability.EXPERIMENT_RUN,
                goal="Produce metrics",
                rationale="Return a structured payload",
                inputs=ExperimentRunInput(instructions="Run once"),
            )
        ],
    )


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


def test_attempt_persists_module_payload() -> None:
    result = ModuleResult(
        status=ModuleStatus.COMPLETED,
        summary="done",
        payload={"accuracy": 0.9},
    )
    engine = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([result]),
            )
        },
        store=InMemoryRunStore(),
    )
    _create_run(engine, "run_payload", request(), proposal())
    run = engine.run_until_stable("run_payload")

    attempt = run.workflow.tasks[0].attempts[0]
    assert attempt.payload == {"accuracy": 0.9}
