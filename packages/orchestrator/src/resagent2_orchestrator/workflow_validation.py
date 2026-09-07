"""Pure round-boundary checks shared by compilation and graph acceptance.

Schema validators own shape, identity and DAG consistency. This predicate owns
the execution policy: a request adds work now, with dependencies in that round.
"""

from resagent2_contracts import WorkflowPatch, WorkflowProposal


def validate_workflow_candidate(candidate: WorkflowProposal | WorkflowPatch) -> None:
    """Reject empty work or dependencies outside the candidate; never mutate it."""
    tasks = candidate.tasks if isinstance(candidate, WorkflowProposal) else candidate.add_tasks
    if not tasks:
        raise ValueError("compiler produced an empty task graph")
    task_ids = {task.id for task in tasks}
    for task in tasks:
        foreign = [dependency for dependency in task.depends_on if dependency not in task_ids]
        if foreign:
            raise ValueError(
                f"task {task.id} depends on tasks outside the current work request: "
                + ", ".join(foreign)
            )
