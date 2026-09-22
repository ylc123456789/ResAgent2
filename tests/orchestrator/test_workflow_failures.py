"""Task input and dependency failures terminate with persistent, factual outcomes."""

from datetime import UTC, datetime

import pytest

from resagent2_contracts import (
    AgentOwner, AgentResult, ArtifactCandidate, ModuleError, ResearchRequest, RunBudget,
    RunPermissions, TaskProposal, WorkRequest, WorkRequestDraft, WorkflowProposal,
)
from resagent2_orchestrator import (
    InMemoryRunStore, ModuleBinding, ResearchRun, ScriptedModulePort, WorkflowScheduler,
)


def scheduler(tmp_path, proposals, results):
    port = ScriptedModulePort(results)
    engine = WorkflowScheduler(
        bindings={"experiment": ModuleBinding(owner=AgentOwner.EXPERIMENT, port=port)},
        store=InMemoryRunStore(), artifact_root=tmp_path / "artifacts", data_root=tmp_path / "data",
    )
    now = datetime.now(UTC)
    run = ResearchRun(
        run_id="run_failure", status="running", created_at=now, updated_at=now,
        request=ResearchRequest(goal="Evaluate", budget=RunBudget(max_llm_calls=10, timeout_seconds=60),
                                permissions=RunPermissions()),
    )
    engine.store.save(run)
    run = engine.accept_proposal(run.run_id, WorkflowProposal(work_request_id="work_test", tasks=proposals))
    run.work_requests.append(WorkRequest(
        id="work_test", run_id=run.run_id, scientific_session_id="session_test",
        request=WorkRequestDraft(objective="Evaluate the task results", expected_evidence=["analysis"]), status="executing",
        workflow_revision=run.workflow.revision, created_at=now, updated_at=now,
    ))
    engine.store.save(run)
    return engine, port, run.run_id


def task(task_id, dependencies=(), **kwargs):
    return TaskProposal(id=task_id, work_request_id="work_test", workflow_agent_kind="experiment",
                        instruction=f"Complete {task_id}", depends_on=list(dependencies), **kwargs)


@pytest.mark.parametrize("fault", ["missing", "ambiguous"])
def test_future_artifact_binding_failure_persists_failed_attempt(tmp_path, fault):
    engine, port, run_id = scheduler(tmp_path, [
        task("task_producer", output_names=["analysis"]),
        task("task_consumer", ["task_producer"], input_artifact_bindings=[
            {"source_task": "task_producer", "output_selector": "analysis"},
        ]),
        task("task_followup", ["task_consumer"]),
    ], [AgentResult(status="completed", report="Produced analysis", artifacts=[ArtifactCandidate(
        kind="analysis", output_name="analysis", path="analysis.txt", media_type="text/plain",
        summary="Analysis", content="Ready for follow-up",
    )])])
    run = engine.execute_task(run_id, "task_producer")
    source_attempt = run.workflow.tasks[0].attempts[0]
    ref = next(run.artifacts[key] for key in source_attempt.artifact_ids
               if run.artifacts[key].output_name == "analysis")
    # Fault-inject the persisted output registry after the producer was accepted.
    if fault == "missing":
        del run.artifacts[ref.id]
    else:
        duplicate = ref.model_copy(update={"id": "artifact_duplicate"})
        run.artifacts[duplicate.id] = duplicate
        source_attempt.artifact_ids.append(duplicate.id)
    engine.store.save(run)

    result = engine.run_until_stable(run_id)

    assert result == engine.store.load(run_id)
    producer, consumer, followup = result.workflow.tasks
    assert producer.status == "completed"
    assert consumer.status == "failed"
    assert len(consumer.attempts) == 1
    attempt = consumer.attempts[0]
    assert attempt.status == "failed"
    assert attempt.finished_at is not None
    assert attempt.error.code == "contract_error"
    assert not attempt.error.retryable
    assert "missing or ambiguous" in attempt.error.message
    assert followup.status == "blocked"
    assert len(port.requests) == 1
    assert result.work_requests[0].status == "stable"


@pytest.mark.parametrize("order", [
    ["task_d", "task_c", "task_b", "task_a"],
    ["task_c", "task_a", "task_d", "task_b"],
])
@pytest.mark.parametrize("failure_status", ["failed", "blocked"])
def test_unordered_dependency_failure_reaches_terminal_closure(tmp_path, order, failure_status):
    dependencies = {"task_a": [], "task_b": ["task_a"], "task_c": ["task_b"], "task_d": ["task_c"]}
    engine, port, run_id = scheduler(
        tmp_path, [task(key, dependencies[key]) for key in order],
        [AgentResult(status=failure_status, report="Cannot execute", error=ModuleError(
            code="tool_failed", message="Source task cannot complete", retryable=False,
        ))],
    )

    result = engine.run_until_stable(run_id)

    assert result == engine.store.load(run_id)
    expected = {key: failure_status if key == "task_a" else "blocked" for key in order}
    assert {item.id: item.status.value for item in result.workflow.tasks} == expected
    work = result.work_requests[0]
    assert work.status == "stable"
    assert {item.task_id: item.status for item in work.outcome.tasks} == expected
    assert [request.task_id for request in port.requests] == ["task_a"]
    assert all(not item.attempts for item in result.workflow.tasks if item.id != "task_a")
