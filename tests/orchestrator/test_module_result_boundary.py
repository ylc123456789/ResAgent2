"""The replaceable ModulePort must obey both envelope and capability contracts."""

from datetime import UTC, datetime

import pytest

from resagent2_contracts import (
    AgentOwner, ArtifactCandidate, Capability, CodeModifyInput, CodeUnderstandInput,
    ErrorCode, ExperimentRunInput, ModuleError, ModuleResult, ModuleStatus,
    QuestionDraft, ResearchRequest, RunBudget, RunStatus, SessionRef, SessionStatus,
    TaskProposal, TaskStatus, WarningRecord, WorkflowProposal,
)
from resagent2_orchestrator import (
    InMemoryRunStore, ModuleBinding, ResearchRun, ScriptedModulePort, WorkflowScheduler,
)


def _session(status=SessionStatus.COMPLETED):
    now = datetime.now(UTC)
    return SessionRef(
        id="session_boundary", module=AgentOwner.EXPERIMENT, state_uri="session://boundary",
        status=status, created_at=now, updated_at=now,
    )


def _payload(capability):
    if capability == Capability.CODE_UNDERSTAND:
        return {"answer": "Inspected", "evidence_files": ["train.py"]}
    if capability == Capability.CODE_MODIFY:
        return {
            "changed_files": ["train.py"], "patch_path": "changes.patch",
            "verification_passed": True, "verification_results": [{
                "command": "python -m pytest", "exit_code": 0,
                "stdout_path": "verify.stdout", "stderr_path": "verify.stderr",
                "duration_seconds": 0.0,
            }],
        }
    return {"env_id": "resenv_test", "metrics": {"accuracy": 0.9}}


def _execute(tmp_path, result, capability=Capability.EXPERIMENT_RUN):
    inputs = {
        Capability.CODE_UNDERSTAND: CodeUnderstandInput(question="Inspect"),
        Capability.CODE_MODIFY: CodeModifyInput(instructions="Modify"),
        Capability.EXPERIMENT_RUN: ExperimentRunInput(instructions="Measure"),
    }[capability]
    port = ScriptedModulePort([result])
    engine = WorkflowScheduler(
        bindings={capability: ModuleBinding(
            owner=AgentOwner.EXPERIMENT if capability == Capability.EXPERIMENT_RUN else AgentOwner.CODING,
            port=port,
        )},
        store=InMemoryRunStore(), artifact_root=tmp_path / "artifacts", data_root=tmp_path / "data",
    )
    now = datetime.now(UTC)
    engine.store.save(ResearchRun(
        run_id="run_boundary", request=ResearchRequest(goal="Test ModulePort", budget=RunBudget(
            max_tasks=3, max_attempts_per_task=2, max_llm_calls=50, timeout_seconds=60,
        )),
        status=RunStatus.RUNNING, created_at=now, updated_at=now,
    ))
    engine.accept_proposal("run_boundary", WorkflowProposal(
        work_request_id="work_boundary", summary="Test", compilation_rationale="Boundary test",
        tasks=[TaskProposal(
            id="task_boundary", work_request_id="work_boundary", goal="Test",
            capability=capability, inputs=inputs,
        )],
    ))
    run = engine.run_until_stable("run_boundary")
    assert len(port.requests) == 1
    return engine, run, run.workflow.tasks[0].attempts[0]


@pytest.mark.parametrize("capability", list(Capability))
@pytest.mark.parametrize("with_warnings", [False, True])
def test_success_payload_is_checked_for_each_capability(tmp_path, capability, with_warnings):
    result = ModuleResult(
        status=ModuleStatus.COMPLETED_WITH_WARNINGS if with_warnings else ModuleStatus.COMPLETED,
        summary="done", payload=_payload(capability), llm_calls=3,
        warnings=[WarningRecord(code="caveat", message="Limited sample")] if with_warnings else [],
    )
    _, run, attempt = _execute(tmp_path, result, capability)
    assert run.workflow.tasks[0].status == TaskStatus.COMPLETED
    assert attempt.error is None
    assert run.llm_calls_used == 3


@pytest.mark.parametrize("capability", list(Capability))
@pytest.mark.parametrize("payload", [None, {}, {"metrics": 123}])
def test_wrong_payload_is_nonretryable_but_keeps_calls_and_session(tmp_path, capability, payload):
    session = _session()
    result = ModuleResult(
        status=ModuleStatus.COMPLETED, summary="claims success", payload=payload,
        session=session, llm_calls=9,
    )
    engine, run, attempt = _execute(tmp_path, result, capability)
    assert run.workflow.tasks[0].status == TaskStatus.FAILED
    assert attempt.error.code == ErrorCode.CONTRACT_ERROR
    assert attempt.error.retryable is False
    assert attempt.session == session
    assert run.llm_calls_used == 9
    assert engine.run_until_stable(run.run_id).llm_calls_used == 9


def test_constructed_envelope_is_revalidated(tmp_path):
    result = ModuleResult.model_construct(
        status=ModuleStatus.COMPLETED, summary="bypassed constructor",
        payload=_payload(Capability.EXPERIMENT_RUN), llm_calls=-10,
    )
    _, run, attempt = _execute(tmp_path, result)
    assert run.workflow.tasks[0].status == TaskStatus.FAILED
    assert attempt.error.code == ErrorCode.CONTRACT_ERROR
    assert attempt.error.retryable is False
    assert run.llm_calls_used == 0


@pytest.mark.parametrize("status", [ModuleStatus.FAILED, ModuleStatus.BLOCKED, ModuleStatus.NEEDS_USER_INPUT])
def test_non_success_does_not_require_success_payload(tmp_path, status):
    if status == ModuleStatus.NEEDS_USER_INPUT:
        result = ModuleResult(
            status=status, summary="Need an answer", session=_session(SessionStatus.PAUSED),
            question=QuestionDraft(text="Which metric?", requested_fields=["metric"], reason="User preference"),
        )
    else:
        result = ModuleResult(
            status=status, summary="Cannot execute",
            error=ModuleError(code=ErrorCode.TOOL_FAILED, message="Original error", retryable=False),
        )
    _, run, attempt = _execute(tmp_path, result)
    assert run.workflow.tasks[0].status.value == status.value
    assert attempt.payload is None
    if status != ModuleStatus.NEEDS_USER_INPUT:
        assert attempt.error.code == ErrorCode.TOOL_FAILED


@pytest.mark.parametrize("failed", [False, True])
def test_registration_failure_preserves_diagnostics_calls_and_prior_artifact(tmp_path, failed):
    original = ModuleError(
        code=ErrorCode.TOOL_FAILED, message="Training crashed", retryable=True,
        details={"stderr_tail": "NameError: totla"},
    ) if failed else None
    session = _session(SessionStatus.FAILED if failed else SessionStatus.COMPLETED)
    result = ModuleResult(
        status=ModuleStatus.FAILED if failed else ModuleStatus.COMPLETED_WITH_WARNINGS,
        summary="Training failed" if failed else "Training finished",
        payload=None if failed else _payload(Capability.EXPERIMENT_RUN),
        error=original, session=session, llm_calls=9,
        warnings=[WarningRecord(code="partial_output", message="Only partial evidence available")],
        artifacts=[
            ArtifactCandidate(kind="experiment_result", path="diagnosis.txt", media_type="text/plain",
                              summary="Available diagnostic", content="NameError: totla"),
            ArtifactCandidate(kind="experiment_result", path="missing.json", media_type="application/json",
                              summary="Missing file"),
        ],
    )
    engine, run, attempt = _execute(tmp_path, result)
    assert run.workflow.tasks[0].status == TaskStatus.FAILED
    assert run.llm_calls_used == 9
    assert attempt.session == session
    assert attempt.payload == result.payload or (not failed and attempt.payload["env_id"] == "resenv_test")
    assert run.workflow.tasks[0].warnings == result.warnings
    assert attempt.error.retryable is False
    assert len(attempt.artifact_ids) == len(run.artifacts) == 1
    assert attempt.artifact_ids[0] in run.artifacts
    if failed:
        assert attempt.error.code == original.code
        assert attempt.error.message == original.message
        assert attempt.error.details["stderr_tail"] == "NameError: totla"
        assert "workspace grant" in attempt.error.details["artifact_registration_error"]
    else:
        assert attempt.error.code == ErrorCode.ARTIFACT_MISSING
    persisted = engine.run_until_stable(run.run_id)
    assert persisted.llm_calls_used == 9
    assert persisted.workflow.tasks[0].warnings == result.warnings
