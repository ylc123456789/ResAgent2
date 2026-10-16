"""Deterministic, stateless organization of original task reports and outputs."""
from typing import Protocol

from resagent2_contracts import (
    AttemptStatus, ModuleError, ErrorCode, WorkRequest, WorkRequestStatus,
    WorkResult, WorkTaskResult, WorkAttemptResult,
)


class WorkInterpreter(Protocol):
    def interpret(self, work: WorkRequest, *, tasks, artifacts) -> WorkResult: ...


class DeterministicWorkInterpreter:
    """Deliver registered refs and truthful execution navigation without rewriting reports."""

    def interpret(self, work, *, tasks, artifacts):
        if work.status not in {WorkRequestStatus.STABLE, WorkRequestStatus.FAILED, WorkRequestStatus.CANCELLED}:
            raise ValueError("only terminal work can be delivered")
        task_results = []
        refs = {}
        for task in tasks:
            if task.work_request_id != work.id:
                continue
            attempts = []
            for attempt in task.attempts:
                if attempt.status in {AttemptStatus.RUNNING, AttemptStatus.NEEDS_USER_INPUT}:
                    raise ValueError("work result cannot deliver a nonterminal Attempt")
                if attempt.report_artifact_id is None:
                    raise ValueError("terminal Attempt lacks its registered original report")
                ids = [attempt.report_artifact_id, *attempt.artifact_ids]
                for artifact_id in ids:
                    ref = artifacts.get(artifact_id)
                    if ref is None or (ref.run_id, ref.task_id, ref.attempt_number) != (work.run_id, task.id, attempt.number):
                        raise ValueError("work artifact has no matching producing Attempt")
                    refs[artifact_id] = ref
                attempts.append(WorkAttemptResult(number=attempt.number, status=attempt.status,
                                                  report_artifact_id=attempt.report_artifact_id,
                                                  artifact_ids=attempt.artifact_ids, error=attempt.error,
                                                  warnings=attempt.warnings, reception_error=attempt.reception_error))
            error = task.attempts[-1].error if task.attempts else None
            if task.status.value in {"failed", "blocked"} and error is None:
                error = ModuleError(code=ErrorCode.CONTRACT_ERROR, message="dependency did not complete; task not executed", retryable=False)
            task_results.append(WorkTaskResult(task_id=task.id, workflow_agent_kind=task.workflow_agent_kind,
                                              status=task.status, instruction=task.instruction,
                                              error=error, warnings=task.warnings, attempts=attempts))
        return WorkResult(run_id=work.run_id, session_id=work.scientific_session_id,
                          work_request_id=work.id, objective=work.request.objective,
                          status=work.status, workflow_revision=work.workflow_revision,
                          error=work.error, tasks=task_results, artifacts=list(refs.values()))
