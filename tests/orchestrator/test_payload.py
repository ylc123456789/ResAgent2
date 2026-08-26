from resagent2_contracts import (
    AgentOwner,
    Capability,
    ExperimentRunInput,
    ModuleResult,
    ModuleStatus,
    ResearchRequest,
    RunBudget,
    SuccessCriterion,
    TaskProposal,
    VerificationMode,
    WorkflowProposal,
)
from resagent2_orchestrator import (
    InMemoryRunStore,
    ModuleBinding,
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
        summary="one task",
        scientific_rationale="Payload persistence test",
        tasks=[
            TaskProposal(
                id="task_experiment",
                capability=Capability.EXPERIMENT_RUN,
                goal="Produce metrics",
                rationale="Return a structured payload",
                inputs=ExperimentRunInput(instructions="Run once"),
                success_criteria=[
                    SuccessCriterion(
                        description="metrics returned",
                        verification=VerificationMode.AUTOMATIC,
                        evidence_key="metrics",
                    )
                ],
            )
        ],
    )


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
    engine.create_run("run_payload", request(), proposal())
    run = engine.run_until_stable("run_payload")

    attempt = run.workflow.tasks[0].attempts[0]
    assert attempt.payload == {"accuracy": 0.9}
