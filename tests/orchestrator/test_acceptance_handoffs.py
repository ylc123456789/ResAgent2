"""Immutable requirements constrain registered outputs of the accepted Attempt."""

import json
from datetime import UTC, datetime

import pytest

from resagent2_contracts import (
    AgentOwner,
    AgentResult,
    ArtifactCandidate,
    ResearchRequest,
    RunBudget,
    TaskAcceptanceSpec,
    TaskProposal,
    WorkflowProposal,
)
from resagent2_orchestrator import (
    ArtifactRegistrationError, InMemoryRunStore, ModuleBinding, ResearchRun,
    ScriptedModulePort, WorkflowScheduler,
)
from resagent2_orchestrator.handoffs import check_acceptance, read_json


def metrics(value=0.9, *, output_name="scores", path="metrics.json"):
    return ArtifactCandidate(kind="metrics", path=path, output_name=output_name,
        media_type="application/json", summary="Accuracy", content=json.dumps({"accuracy": value}))


def accepted(tmp_path, outputs, *, spec=None):
    port = ScriptedModulePort([AgentResult(status="completed", report="Measured accuracy", artifacts=outputs)])
    scheduler = WorkflowScheduler(
        bindings={"experiment": ModuleBinding(owner=AgentOwner.EXPERIMENT, port=port)},
        store=InMemoryRunStore(), artifact_root=tmp_path / "artifacts", data_root=tmp_path / "data",
    )
    now = datetime.now(UTC)
    run = ResearchRun(run_id="run_acceptance", status="running",
        request=ResearchRequest(goal="Evaluate", budget=RunBudget(max_tasks=2,
            max_attempts_per_task=2, max_llm_calls=10, timeout_seconds=60)),
        created_at=now, updated_at=now)
    scheduler.store.save(run)
    proposal = WorkflowProposal(work_request_id="work_1", tasks=[TaskProposal(
        id="task_measure", work_request_id="work_1", instruction="Measure accuracy",
        workflow_agent_kind="experiment", output_names=["scores"],
        acceptance_spec=spec or TaskAcceptanceSpec(required_metric_keys=["accuracy"]),
    )])
    run = scheduler.accept_proposal(run.run_id, proposal)
    return scheduler, port, run, proposal


def test_acceptance_is_frozen_and_shared_by_task_attempt_and_request(tmp_path):
    scheduler, port, run, proposal = accepted(tmp_path, [metrics()])
    requirement = run.workflow.tasks[0].acceptance_ref
    assert requirement == run.artifacts[requirement.id]
    assert read_json(requirement, TaskAcceptanceSpec).required_output_names == ["scores"]
    proposal.tasks[0].acceptance_spec.required_metric_keys.append("invented_after_acceptance")
    completed = scheduler.run_until_stable(run.run_id)
    task = completed.workflow.tasks[0]
    assert task.status == "completed"
    assert task.acceptance_ref == requirement == task.attempts[0].acceptance_ref
    assert requirement in port.requests[0].input_artifacts
    assert read_json(requirement, TaskAcceptanceSpec).required_metric_keys == ["accuracy"]


@pytest.mark.parametrize("value", [True, "0.9", None, float("nan"), float("inf")])
def test_required_metrics_must_be_finite_numbers(tmp_path, value):
    scheduler, _, run, _ = accepted(tmp_path, [metrics(value)])
    failed = scheduler.run_until_stable(run.run_id)
    task = failed.workflow.tasks[0]
    assert task.status == "failed"
    assert "required numeric metric keys" in task.attempts[0].error.message


@pytest.mark.parametrize("outputs,diagnostic", [
    ([metrics(output_name=None)], "required logical outputs"),
    ([metrics(), metrics(path="other.json")], "duplicate logical output name"),
])
def test_declared_output_names_require_one_registered_artifact(tmp_path, outputs, diagnostic):
    scheduler, _, run, _ = accepted(tmp_path, outputs)
    failed = scheduler.run_until_stable(run.run_id)
    task = failed.workflow.tasks[0]
    assert task.status == "failed"
    assert diagnostic in task.attempts[0].error.message
    assert task.attempts[0].artifact_ids  # Partial output remains available for diagnosis.


def test_attempt_cannot_replace_its_accepted_requirement(tmp_path):
    scheduler, _, run, _ = accepted(tmp_path, [metrics()])
    completed = scheduler.run_until_stable(run.run_id)
    task = completed.workflow.tasks[0]
    attempt = task.attempts[0].model_copy(update={"acceptance_ref": None})
    with pytest.raises(ArtifactRegistrationError, match="binding differs"):
        check_acceptance(completed, task, attempt, [completed.artifacts[key] for key in attempt.artifact_ids])


def test_successful_execution_requires_a_current_execution_record(tmp_path):
    spec = TaskAcceptanceSpec(required_metric_keys=["accuracy"], require_successful_execution=True)
    scheduler, _, run, _ = accepted(tmp_path, [metrics()], spec=spec)
    failed = scheduler.run_until_stable(run.run_id)
    assert "required successful execution" in failed.workflow.tasks[0].attempts[0].error.message


@pytest.mark.parametrize("commands,success", [
    ([("python train.py", 1, False), ("python --version", 0, False)], False),
    ([("python train.py", 0, True), ("python --version", 0, False)], False),
    ([("python train.py", 1, False), ("python train.py --smoke", 0, False)], False),
    ([("python train.py", 1, False), ('python "train.py"', 0, False)], True),
    ([("python train.py", 1, False), ("python other.py", 1, False),
      ("python train.py", 0, False)], False),
])
def test_execution_acceptance_cannot_hide_failure_with_unrelated_success(tmp_path, commands, success):
    results = [dict(command=command, exit_code=code, timed_out=timed_out,
                    stdout_path=f"{index}.stdout", stderr_path=f"{index}.stderr", duration_seconds=0.1)
               for index, (command, code, timed_out) in enumerate(commands)]
    execution = ArtifactCandidate(kind="execution_record", path="execution_record.json",
        media_type="application/json", summary="Actual executions", content=json.dumps({"results": results}))
    # A successful verification must not override an unresolved experiment failure either.
    verification = ArtifactCandidate(kind="verification_result", path="verification.json",
        media_type="application/json", summary="Current syntax check", content=json.dumps({
            "covers_current_workspace": True, "results": [dict(command="python -m py_compile train.py",
                exit_code=0, timed_out=False, stdout_path="verify.stdout", stderr_path="verify.stderr", duration_seconds=0.1)]}))
    scheduler, _, run, _ = accepted(tmp_path, [metrics(), execution, verification],
        spec=TaskAcceptanceSpec(require_successful_execution=True, required_metric_keys=["accuracy"]))
    outcome = scheduler.run_until_stable(run.run_id)
    task = outcome.workflow.tasks[0]
    assert task.status == ("completed" if success else "failed")
    if not success:
        assert "required successful execution" in task.attempts[0].error.message


def test_registry_derives_required_paths_and_ignores_claimed_metadata(tmp_path):
    from resagent2_contracts import WorkspaceGrant
    from resagent2_orchestrator import ArtifactRegistry
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "actual.json").write_text('{"accuracy":0.9}')
    registry = ArtifactRegistry(tmp_path / "registered")
    common = dict(grant=WorkspaceGrant(root=str(workspace), mode="read_only", source="local"),
                  producer="experiment", run_id="run_test", task_id="task_test", attempt_number=1, existing_ids=set())
    candidate = metrics(path="actual.json").model_copy(update={"content": None, "metadata": {"source_path": "fake.json"}})
    ref = registry.register(candidate, index=1, **common)
    assert ref.metadata["source_path"] == "actual.json"
    inline = registry.register(candidate.model_copy(update={"content": "{}"}), index=2, **common)
    assert "source_path" not in inline.metadata


def test_registry_accepts_controlled_output_files_but_rejects_escape_and_ambiguity(tmp_path):
    from resagent2_contracts import WorkspaceGrant
    from resagent2_orchestrator import ArtifactRegistry
    workspace, output = tmp_path / "workspace", tmp_path / "output"
    workspace.mkdir()
    output.mkdir()
    (output / "metrics.json").write_text('{"accuracy":0.9}')
    registry = ArtifactRegistry(tmp_path / "registered")
    candidate = metrics().model_copy(update={"content": None})
    common = dict(grant=WorkspaceGrant(root=str(workspace), mode="read_only", source="local"),
                  output_dir=str(output), producer="experiment", run_id="run_test", task_id="task_test",
                  attempt_number=1, existing_ids=set())
    ref = registry.register(candidate, index=1, **common)
    assert ref.metadata["source_root"] == "output_dir"
    (workspace / "metrics.json").write_text("{}")
    with pytest.raises(ArtifactRegistrationError, match="ambiguous"):
        registry.register(candidate, index=2, **common)
    (workspace / "metrics.json").unlink()
    (output / "metrics.json").unlink()
    (tmp_path / "outside.json").write_text("{}")
    (output / "metrics.json").symlink_to(tmp_path / "outside.json")
    with pytest.raises(ArtifactRegistrationError, match="outside"):
        registry.register(candidate, index=2, **common)
