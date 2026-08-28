"""ResearchController: orchestrate the scientific control loop.

It composes existing components — the ScientificPort, the WorkflowCompiler and
the WorkflowScheduler — into a natural-language research run. It is a thin
coordinator: it never rewrites the scheduler, never reads a child Session's
private state, and never decides state transitions the deterministic code
already owns.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from resagent2_contracts import (
    CapabilityRegistry,
    ErrorCode,
    ModuleError,
    PendingQuestion,
    ResearchRequest,
    RunStatus,
    ScientificCompletedResult,
    ScientificFailedResult,
    ScientificQuestionResult,
    ScientificTurnRequest,
    ScientificTurnResult,
    ScientificWorkRequestResult,
    TaskBudget,
    UserAnswer,
    WorkRequest,
    WorkRequestStatus,
    WorkTaskOutcome,
)

from .compiler import WorkflowCompiler
from .models import ResearchRun
from .scheduler import WorkflowScheduler


class ScientificPort(Protocol):
    """Boundary the controller uses to drive one Scientific turn."""

    def run(self, request: ScientificTurnRequest) -> ScientificTurnResult: ...


class ScientificGate(Protocol):
    """Validate a completed Scientific turn against the run state.

    Phase 7.5 wires a minimal gate; Phase 7.6 replaces it with the full
    ScientificCompletionValidator (CONTRACTS §20.10.2).
    """

    def validate(
        self,
        run: ResearchRun,
        result: ScientificCompletedResult,
    ) -> list[str]:
        """Return an empty list on success, else human-readable violations."""


class ResearchController:
    """Drive one research run from a natural-language goal to a final opinion."""

    def __init__(
        self,
        *,
        scientific_port: ScientificPort,
        compiler: WorkflowCompiler,
        scheduler: WorkflowScheduler,
        registry: CapabilityRegistry,
        gate: ScientificGate | None = None,
    ) -> None:
        self.scientific_port = scientific_port
        self.compiler = compiler
        self.scheduler = scheduler
        self.registry = registry
        self.gate = gate or _MinimalGate()

    def create_run(
        self,
        run_id: str,
        request: ResearchRequest,
    ) -> ResearchRun:
        if self.scheduler.store.exists(run_id):
            raise ValueError(f"run already exists: {run_id}")
        now = datetime.now(UTC)
        run = ResearchRun(
            run_id=run_id,
            request=request,
            status=RunStatus.RUNNING,
            created_at=now,
            updated_at=now,
        )
        self._save(run)
        return self.run_until_stable(run_id)

    def answer_question(self, run_id: str, answer: UserAnswer) -> ResearchRun:
        run = self.scheduler.store.load(run_id)
        question = run.pending_question
        if question is None or answer.question_id != question.id:
            raise ValueError("answer does not match pending question")
        missing = set(question.requested_fields) - set(answer.values)
        if missing:
            raise ValueError(f"answer is missing fields: {sorted(missing)}")
        run.answers.append(answer)
        run.pending_question = None
        run.status = RunStatus.RUNNING
        self._save(run)
        return self.run_until_stable(run_id)

    def run_until_stable(self, run_id: str) -> ResearchRun:
        while True:
            run = self.scheduler.store.load(run_id)
            if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.PAUSED}:
                return run

            turn_result = self._scientific_turn(run)
            run = self._apply_turn(run_id, turn_result)
            if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.PAUSED}:
                return run

    def _scientific_turn(self, run: ResearchRun) -> ScientificTurnResult:
        work_outcome = None
        parent_session_id = run.scientific_session.id if run.scientific_session else None
        active = self._active_work_request(run)
        if active is not None and active.status == WorkRequestStatus.STABLE:
            work_outcome = active.outcome
            parent_session_id = active.scientific_session_id
            # Mark consumed before the resume; the same WorkOutcome must never
            # be delivered twice (CONTRACTS §20.7 idempotency).
            active.status = WorkRequestStatus.CONSUMED
            self._save(run)
        return self.scientific_port.run(
            ScientificTurnRequest(
                run_id=run.run_id,
                research=run.request,
                authorized_artifacts=self._authorized_artifacts(run),
                work_outcome=work_outcome,
                unresolved_task_outcomes=self._unresolved_tasks(run),
                answers=list(run.answers),
                budget=TaskBudget(
                    max_steps=run.request.budget.max_llm_calls,
                    max_llm_calls=run.request.budget.max_llm_calls,
                    timeout_seconds=run.request.budget.timeout_seconds,
                ),
                parent_session_id=parent_session_id,
            )
        )

    def _apply_turn(self, run_id: str, result: ScientificTurnResult) -> ResearchRun:
        run = self.scheduler.store.load(run_id)

        if isinstance(result, ScientificWorkRequestResult):
            self._merge_observed(run, result.observed_artifact_ids)
            run.latest_scientific_assessment = result.assessment
            run.scientific_session = result.session
            now = datetime.now(UTC)
            run.work_requests.append(
                WorkRequest(
                    id=f"work_{len(run.work_requests) + 1}",
                    run_id=run.run_id,
                    scientific_session_id=result.session.id,
                    request=result.work_request,
                    status=WorkRequestStatus.REQUESTED,
                    created_at=now,
                    updated_at=now,
                )
            )
            self._save(run)
            return self._execute_work_request(run_id)

        if isinstance(result, ScientificQuestionResult):
            self._merge_observed(run, result.observed_artifact_ids)
            run.latest_scientific_assessment = result.assessment
            run.scientific_session = result.session
            run.pending_question = PendingQuestion(
                id=f"question_sci_{len(run.answers)}",
                run_id=run.run_id,
                text=result.question.text,
                requested_fields=result.question.requested_fields,
                created_at=datetime.now(UTC),
            )
            run.status = RunStatus.PAUSED
            self._save(run)
            return run

        if isinstance(result, ScientificCompletedResult):
            self._merge_observed(run, result.observed_artifact_ids)
            violations = self.gate.validate(run, result)
            if violations:
                run.status = RunStatus.FAILED
                self._save(run)
                return run
            run.final_opinion = result.opinion
            run.scientific_session = result.session
            run.status = RunStatus.COMPLETED
            self._save(run)
            return run

        # ScientificFailedResult
        assert isinstance(result, ScientificFailedResult)
        self._merge_observed(run, result.observed_artifact_ids)
        if result.session is not None:
            run.scientific_session = result.session
        run.status = RunStatus.FAILED
        self._save(run)
        return run

    def _execute_work_request(self, run_id: str) -> ResearchRun:
        run = self.scheduler.store.load(run_id)
        active = self._active_work_request(run)
        if active is None:
            run.status = RunStatus.FAILED
            self._save(run)
            return run

        active.status = WorkRequestStatus.COMPILING
        self._save(run)

        try:
            compiled = self.compiler.compile(
                active,
                current=run.workflow,
                registry=self.registry,
                budget=run.request.budget,
            )
        except Exception as error:
            active.status = WorkRequestStatus.FAILED
            active.error = ModuleError(
                code=ErrorCode.CONTRACT_ERROR,
                message=f"compilation failed: {error}",
                retryable=False,
            )
            run.status = RunStatus.FAILED
            self._save(run)
            return run

        if run.workflow is None:
            self.scheduler.accept_proposal(run_id, compiled)
        else:
            self.scheduler.apply_patch(run_id, compiled)
        run = self.scheduler.store.load(run_id)
        active = self._active_work_request(run)
        active.status = WorkRequestStatus.EXECUTING
        active.workflow_revision = run.workflow.revision
        self._save(run)

        return self.scheduler.run_until_stable(run_id)

    def _active_work_request(self, run: ResearchRun) -> WorkRequest | None:
        for work_request in run.work_requests:
            if work_request.status in {
                WorkRequestStatus.REQUESTED,
                WorkRequestStatus.COMPILING,
                WorkRequestStatus.EXECUTING,
                WorkRequestStatus.STABLE,
            }:
                return work_request
        return None

    def _merge_observed(self, run: ResearchRun, observed: list[str]) -> None:
        current = set(run.scientific_observed_artifact_ids)
        for artifact_id in observed:
            current.add(artifact_id)
        run.scientific_observed_artifact_ids = sorted(current)

    def _authorized_artifacts(self, run: ResearchRun):
        return [run.artifacts[artifact_id] for artifact_id in run.artifacts]

    def _unresolved_tasks(self, run: ResearchRun) -> list[WorkTaskOutcome]:
        active = self._active_work_request(run)
        if active is None or active.outcome is None:
            return []
        return [
            task for task in active.outcome.tasks if task.status in {"failed", "blocked"}
        ]

    def _save(self, run: ResearchRun) -> None:
        run.updated_at = datetime.now(UTC)
        self.scheduler.store.save(ResearchRun.model_validate(run.model_dump()))


class _MinimalGate:
    """Phase 7.5 placeholder: accept any field-valid completed opinion."""

    def validate(self, run: ResearchRun, result: ScientificCompletedResult) -> list[str]:
        return []
