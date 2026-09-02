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
    AgentOwner,
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
    SessionRef,
    SessionStatus,
    TaskBudget,
    UserAnswer,
    WorkRequest,
    WorkRequestStatus,
    WorkTaskOutcome,
    WorkspaceDescriptor,
)

from .compiler import WorkflowCompiler
from .completion import (
    CompletionValidation,
    FinalReportRenderer,
    ScientificCompletionValidator,
)
from .models import ResearchRun
from .scheduler import (
    WorkflowScheduler,
    _transition_work_request,
    _validate_answer,
)


class ScientificPort(Protocol):
    """Boundary the controller uses to drive one Scientific turn."""

    def run(self, request: ScientificTurnRequest) -> ScientificTurnResult: ...


class ScientificGate(Protocol):
    """Validate a completed Scientific turn against the run state."""

    def validate(
        self,
        run: ResearchRun,
        result: ScientificCompletedResult,
    ) -> CompletionValidation: ...


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
        report_renderer: FinalReportRenderer | None = None,
    ) -> None:
        self.scientific_port = scientific_port
        self.compiler = compiler
        self.scheduler = scheduler
        self.registry = registry
        self.gate = gate or ScientificCompletionValidator(registry)
        self.report_renderer = report_renderer or FinalReportRenderer()

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
        # Freeze caller-supplied inputs into registered orchestrator Artifacts
        # before any Scientific turn or task can observe them (ADR-0011 §4).
        for spec in request.input_artifacts:
            artifact = self.scheduler.artifact_registry.register_import(
                spec, run_id=run_id
            )
            run.artifacts[artifact.id] = artifact
        self._save(run)
        return self.run_until_stable(run_id)

    def answer_question(self, run_id: str, answer: UserAnswer) -> ResearchRun:
        """The single user-answer entry (ADR-0011 §1).

        A Scientific question (``task_id is None``) only feeds the Scientific
        turn; a task question also resumes the paused Attempt on the same
        number, Session and output_dir.
        """
        run = self.scheduler.store.load(run_id)
        question = run.pending_question
        _validate_answer(question, answer)
        assert question is not None
        run.answers.append(answer)
        run.pending_question = None
        run.status = RunStatus.RUNNING
        task_id = question.task_id
        if task_id is not None:
            run.answer_task_ids[answer.question_id] = task_id
            # Resume the paused task in the SAME ResearchRun object so the answer
            # and the task transition are saved atomically in one snapshot.
            self.scheduler.resume_task_in_place(run, task_id)
        self._save(run)
        return self.run_until_stable(run_id)

    def run_until_stable(self, run_id: str) -> ResearchRun:
        while True:
            run = self.scheduler.store.load(run_id)
            if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.PAUSED}:
                return run

            # This is the only recovery entry for a persisted ResearchRun.
            # Scheduler invocation is synchronous in the current local model,
            # so a RUNNING Attempt observed after a new controller entry is a
            # stale intent record, not a live worker claim.
            if self.scheduler._recover_interrupted_attempts_in_place(run):
                self._save(run)
                continue

            # Wall-clock deadline: fail deterministically when the run budget is
            # exhausted (ADR-0011 §7.3). Preemptive per-tool cancellation is out
            # of scope; this is the run-level remaining-timeout gate.
            elapsed = (datetime.now(UTC) - run.created_at).total_seconds()
            if elapsed >= run.request.budget.timeout_seconds:
                run.status = RunStatus.FAILED
                self._save(run)
                return run

            if run.llm_calls_used >= run.request.budget.max_llm_calls:
                run.status = RunStatus.FAILED
                self._save(run)
                return run

            active = self._active_work_request(run)
            if active is not None and active.status != WorkRequestStatus.STABLE:
                # A work request is still being compiled or executed; resume
                # that before running another Scientific turn.
                run = self._execute_work_request(run_id)
                if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.PAUSED}:
                    return run
                continue

            self._bind_initial_scientific_session(run)
            turn_result = self._scientific_turn(run)
            run = self._apply_turn(run_id, turn_result)
            if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.PAUSED}:
                return run

    def _scientific_turn(self, run: ResearchRun) -> ScientificTurnResult:
        work_outcome = None
        previous_work_request = None
        parent_session_id = None
        active = self._active_work_request(run)
        if active is not None and active.status == WorkRequestStatus.STABLE:
            work_outcome = active.outcome
            previous_work_request = active.request
            parent_session_id = active.scientific_session_id
        elif (
            run.scientific_session is not None
            and run.scientific_session.status == SessionStatus.PAUSED
        ):
            # User answers resume a deliberate pause. An ACTIVE session is an
            # interrupted first turn; ScientificAgent reopens its deterministic
            # checkpoint without pretending it was paused.
            parent_session_id = run.scientific_session.id
        remaining = run.request.budget.max_llm_calls - run.llm_calls_used
        elapsed = (datetime.now(UTC) - run.created_at).total_seconds()
        remaining_timeout = max(1, int(run.request.budget.timeout_seconds - elapsed))
        return self.scientific_port.run(
            ScientificTurnRequest(
                run_id=run.run_id,
                research=run.request,
                authorized_artifacts=self._authorized_artifacts(run),
                work_outcome=work_outcome,
                previous_work_request=previous_work_request,
                unresolved_task_outcomes=self._unresolved_tasks(run),
                answers=self._pending_answers(run),
                budget=TaskBudget(
                    max_steps=remaining,
                    max_llm_calls=remaining,
                    timeout_seconds=remaining_timeout,
                ),
                parent_session_id=parent_session_id,
            )
        )

    def _bind_initial_scientific_session(self, run: ResearchRun) -> None:
        """Persist the deterministic Scientific session reference before use.

        A first-turn crash can then be recovered without losing ownership of
        ``session_scientific_<run_id>``. The runtime owns the session contents;
        the controller stores only this reference and never reads its memory.
        """
        if run.scientific_session is not None:
            return
        now = datetime.now(UTC)
        active = self._active_work_request(run)
        session_id = (
            active.scientific_session_id
            if active is not None
            else f"session_scientific_{run.run_id}"
        )
        run.scientific_session = SessionRef(
            id=session_id,
            module=AgentOwner.SCIENTIFIC,
            state_uri=f"session://{session_id}",
            # A persisted WorkRequest was produced by request_work, whose
            # AgentLoop boundary deliberately pauses the Scientific session.
            # This also repairs the narrow crash window before the controller
            # copied that SessionRef into the Run.
            status=SessionStatus.PAUSED if active is not None else SessionStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        self._save(run)

    def _apply_turn(self, run_id: str, result: ScientificTurnResult) -> ResearchRun:
        run = self.scheduler.store.load(run_id)

        # The ScientificPort returned, so the delivered WorkOutcome is consumed.
        # This runs only after a successful return; a crash before this point
        # leaves the work request stable so a restart redelivers it (and the
        # ScientificPort idempotency returns the same result).
        for work_request in run.work_requests:
            if work_request.status == WorkRequestStatus.STABLE:
                _transition_work_request(work_request, WorkRequestStatus.CONSUMED)

        violations = self._review_observed(run, result.observed_artifact_ids)
        if violations:
            run.status = RunStatus.FAILED
            self._save(run)
            return run

        self._merge_observed(run, result.observed_artifact_ids)
        self._accumulate_llm_calls(run, result.llm_calls)
        self._mark_answers_delivered(run)

        if run.llm_calls_used > run.request.budget.max_llm_calls:
            run.status = RunStatus.FAILED
            self._save(run)
            return run

        if isinstance(result, ScientificWorkRequestResult):
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
            validation = self.gate.validate(run, result)
            run.scientific_session = result.session
            if not validation.ok:
                run.completion_violations = list(validation.violations)
                run.status = RunStatus.FAILED
                self._save(run)
                return run
            assert validation.report is not None
            try:
                rendered = self.report_renderer.render(validation.report)
                report = self.scheduler.artifact_registry.register_final_report(
                    rendered.candidate,
                    rendered.content,
                    run_id=run.run_id,
                )
            except Exception:
                run.status = RunStatus.FAILED
                self._save(run)
                return run
            run.artifacts[report.id] = report
            run.completion_violations = []
            run.final_opinion = result.opinion
            run.final_report_artifact_id = report.id
            run.status = RunStatus.COMPLETED
            self._save(run)
            return run

        # ScientificFailedResult
        assert isinstance(result, ScientificFailedResult)
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

        if active.status == WorkRequestStatus.EXECUTING:
            # The workflow was already accepted; resume scheduler execution.
            return self.scheduler.run_until_stable(run_id)

        if active.status == WorkRequestStatus.COMPILING and run.workflow is not None:
            accepted = run.workflow.created_from == active.id or any(
                task.work_request_id == active.id for task in run.workflow.tasks
            )
            if accepted:
                # Recovery window: the graph was durably accepted immediately
                # before the WorkRequest transition was saved.
                _transition_work_request(
                    active,
                    WorkRequestStatus.EXECUTING,
                    workflow_revision=run.workflow.revision,
                )
                self._save(run)
                return self.scheduler.run_until_stable(run_id)

        # REQUESTED or COMPILING: the compiler is stateless, so a crash after
        # marking COMPILING is safely retried. Only REQUESTED needs a
        # transition; an already-COMPILING request (crash between the COMPILING
        # save and the compile) is recompiled directly, never via a forbidden
        # COMPILING -> COMPILING migration.
        if active.status == WorkRequestStatus.REQUESTED:
            _transition_work_request(active, WorkRequestStatus.COMPILING)
            self._save(run)

        remaining_calls = run.request.budget.max_llm_calls - run.llm_calls_used
        if remaining_calls <= 0:
            run.status = RunStatus.FAILED
            self._save(run)
            return run

        try:
            compilation = self.compiler.compile(
                active,
                current=run.workflow,
                registry=self.registry,
                budget=run.request.budget,
                workspaces=self._workspace_descriptors(),
                remaining_calls=remaining_calls,
            )
        except Exception as error:
            # Preserve the compiler's consumed calls even when it fails.
            run.llm_calls_used += getattr(self.compiler, "llm_calls", 0)
            _transition_work_request(
                active,
                WorkRequestStatus.FAILED,
                error=ModuleError(
                    code=ErrorCode.CONTRACT_ERROR,
                    message=f"compilation failed: {error}",
                    retryable=False,
                ),
            )
            run.status = RunStatus.FAILED
            self._save(run)
            return run

        run.llm_calls_used += compilation.llm_calls
        self._save(run)
        try:
            if run.workflow is None:
                self.scheduler.accept_proposal(run_id, compilation.output)
            else:
                self.scheduler.apply_patch(run_id, compilation.output)
        except Exception as error:
            run = self.scheduler.store.load(run_id)
            active = self._active_work_request(run)
            if active is not None:
                _transition_work_request(
                    active,
                    WorkRequestStatus.FAILED,
                    error=ModuleError(
                        code=ErrorCode.CONTRACT_ERROR,
                        message=f"compiled workflow was rejected: {error}",
                        retryable=False,
                    ),
                )
            run.status = RunStatus.FAILED
            self._save(run)
            return run
        run = self.scheduler.store.load(run_id)
        active = self._active_work_request(run)
        if active is None:
            run.status = RunStatus.FAILED
            self._save(run)
            return run
        _transition_work_request(
            active,
            WorkRequestStatus.EXECUTING,
            workflow_revision=run.workflow.revision,
        )
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

    def _review_observed(self, run: ResearchRun, observed: list[str]) -> list[str]:
        """Return the observed ids that are not registered artifacts of this run."""
        violations: list[str] = []
        for artifact_id in observed:
            artifact = run.artifacts.get(artifact_id)
            if artifact is None or artifact.run_id != run.run_id:
                violations.append(artifact_id)
        return violations

    def _merge_observed(self, run: ResearchRun, observed: list[str]) -> None:
        current = set(run.scientific_observed_artifact_ids)
        for artifact_id in observed:
            if artifact_id in run.artifacts:
                current.add(artifact_id)
        run.scientific_observed_artifact_ids = sorted(current)

    def _pending_answers(self, run: ResearchRun) -> list[UserAnswer]:
        delivered = set(run.delivered_answer_ids)
        # Task-level answers are delivered to their task via answer_task_ids,
        # never to the Scientific turn (ADR-0011 §1).
        return [
            a
            for a in run.answers
            if a.question_id not in delivered and a.question_id not in run.answer_task_ids
        ]

    def _mark_answers_delivered(self, run: ResearchRun) -> None:
        run.delivered_answer_ids = [
            a.question_id
            for a in run.answers
            if a.question_id not in run.answer_task_ids
        ]

    def _accumulate_llm_calls(self, run: ResearchRun, calls: int) -> None:
        run.llm_calls_used += calls

    def _authorized_artifacts(self, run: ResearchRun):
        return [run.artifacts[artifact_id] for artifact_id in run.artifacts]

    def _unresolved_tasks(self, run: ResearchRun) -> list[WorkTaskOutcome]:
        if run.workflow is None:
            return []
        unresolved: list[WorkTaskOutcome] = []
        for task in run.workflow.tasks:
            if task.status not in {"failed", "blocked"}:
                continue
            last = task.attempts[-1] if task.attempts else None
            unresolved.append(
                WorkTaskOutcome(
                    task_id=task.id,
                    status=task.status.value,
                    summary=task.goal,
                    error=last.error if last else None,
                    warnings=list(task.warnings),
                )
            )
        return unresolved

    def _workspace_descriptors(self) -> list[WorkspaceDescriptor]:
        """Summarize the scheduler's declared workspaces for the compiler."""
        return [
            WorkspaceDescriptor(
                workspace_id=workspace_id,
                source_kind=spec.source_kind,
            )
            for workspace_id, spec in self.scheduler.workspace_specs.items()
        ]

    def _save(self, run: ResearchRun) -> None:
        run.updated_at = datetime.now(UTC)
        self.scheduler.store.save(ResearchRun.model_validate(run.model_dump()))
