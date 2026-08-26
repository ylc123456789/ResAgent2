from resagent2_contracts import Capability, ResearchRequest, RunBudget
from resagent2_orchestrator import DeterministicPlanningPort


def request() -> ResearchRequest:
    return ResearchRequest(
        goal="Evaluate a method",
        budget=RunBudget(
            max_tasks=5,
            max_attempts_per_task=3,
            max_llm_calls=50,
            timeout_seconds=60,
        ),
    )


def test_deterministic_planning_port_emits_task_plane_only() -> None:
    proposal = DeterministicPlanningPort().propose(request())

    assert [task.id for task in proposal.tasks] == [
        "task_code",
        "task_experiment",
        "task_analyze",
    ]
    assert [task.capability for task in proposal.tasks] == [
        Capability.CODE_MODIFY,
        Capability.EXPERIMENT_RUN,
        Capability.SCIENTIFIC_ANALYZE,
    ]
    assert [task.depends_on for task in proposal.tasks] == [
        [],
        ["task_code"],
        ["task_experiment"],
    ]
    assert all(
        task.capability not in {Capability.SCIENTIFIC_PLAN, Capability.ASK_USER}
        for task in proposal.tasks
    )
    assert all(task.required for task in proposal.tasks)
