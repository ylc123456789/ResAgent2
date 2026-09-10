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

from pydantic import BaseModel, TypeAdapter, ValidationError

from resagent2_contracts import (
    AgentOwner,
    CapabilityRegistry,
    DatasetRef,
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
    scientific_session_id,
)

from .compiler import CompilationError, WorkflowCompiler
from .completion import (
    CompletionValidation,
    FinalReportRenderer,
    ScientificCompletionValidator,
    scientific_turn_violations,
)
from .models import ResearchRun
from .scheduler import (
    WorkflowScheduler,
    _transition_work_request,
    _validate_answer,
)

_TURN_RESULT = TypeAdapter(ScientificTurnResult)


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


class DatasetRefSource(Protocol):
    """Deployment resource source; the Controller only consumes references."""

    def references(self) -> list[DatasetRef]: ...


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
        dataset_ref_source: DatasetRefSource | None = None,
    ) -> None:
        self.scientific_port = scientific_port
        self.compiler = compiler
        self.scheduler = scheduler
        self.registry = registry
        self.gate = gate or ScientificCompletionValidator(registry)
        self.report_renderer = report_renderer or FinalReportRenderer()
        self.dataset_ref_source = dataset_ref_source

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
        # Settle the pause in the SAME snapshot as the answer and Task resume.
        # Use the host clock, never the caller-controlled answered_at timestamp.
        run.user_wait_seconds += max(
            0.0, (datetime.now(UTC) - question.created_at).total_seconds()
        )
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
            self._merge_registered_datasets(run)

            # This is the only recovery entry for a persisted ResearchRun.
            # Scheduler invocation is synchronous in the current local model,
            # so a RUNNING Attempt observed after a new controller entry is a
            # stale intent record, not a live worker claim.
            if self.scheduler._recover_interrupted_attempts_in_place(run):
                self._save(run)
                continue

            # Shared wall-clock budget excludes explicit ask_user waits only.
            # This does not preempt or undo an already running external command.
            if run.remaining_timeout_seconds(datetime.now(UTC)) <= 0:
                return self._fail_run(run, ModuleError(
                    code=ErrorCode.TIMEOUT, message="Run execution-time budget exhausted", retryable=False,
                ))

            if run.llm_calls_used >= run.request.budget.max_llm_calls:
                return self._fail_run(run, ModuleError(
                    code=ErrorCode.BUDGET_EXHAUSTED, message="Run LLM-call budget exhausted", retryable=False,
                ))

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

    def _merge_registered_datasets(self, run: ResearchRun) -> None:
        """Add newly registered references without changing prior Run bindings."""

        if self.dataset_ref_source is None:
            return
        existing = {ref.dataset_id: ref for ref in run.dataset_refs}
        additions: list[DatasetRef] = []
        for ref in self.dataset_ref_source.references():
            previous = existing.get(ref.dataset_id)
            if previous is not None:
                if previous != ref:
                    raise ValueError(
                        f"dataset {ref.dataset_id!r} was remapped during the Run"
                    )
                continue
            existing[ref.dataset_id] = ref
            additions.append(ref)
        if not additions:
            return
        run.dataset_refs.extend(additions)
        self._save(run)

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
        remaining_timeout = max(1, int(run.remaining_timeout_seconds(datetime.now(UTC))))
        return self.scientific_port.run(
            ScientificTurnRequest(
                run_id=run.run_id,
                research=run.request,
                dataset_refs=list(run.dataset_refs),
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
        its bounded Session identity. The runtime owns the session contents;
        the controller stores only this reference and never reads its memory.
        """
        if run.scientific_session is not None:
            return
        now = datetime.now(UTC)
        active = self._active_work_request(run)
        session_id = (
            active.scientific_session_id
            if active is not None
            else scientific_session_id(run.run_id)
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

        # A returned response is not yet an accepted response. Account for
        # consumption even when its schema or references are subsequently rejected.
        raw = result.model_dump() if isinstance(result, BaseModel) else result
        try:
            result = _TURN_RESULT.validate_python(raw)
        except ValidationError as error:
            calls = raw.get("llm_calls", 0) if isinstance(raw, dict) else 0
            if isinstance(calls, int) and not isinstance(calls, bool) and calls >= 0:
                run.llm_calls_used += calls
            return self._fail_run(run, ModuleError(
                code=ErrorCode.CONTRACT_ERROR,
                message=f"ScientificPort returned an invalid result: {error}",
                retryable=False,
            ))
        run.llm_calls_used += result.llm_calls
        violations = scientific_turn_violations(run, result)
        if violations:
            run.completion_violations = violations
            return self._fail_run(run, ModuleError(
                code=ErrorCode.CONTRACT_ERROR,
                message="ScientificPort response failed boundary validation",
                retryable=False,
            ))
        if run.llm_calls_used > run.request.budget.max_llm_calls:
            return self._fail_run(run, ModuleError(
                code=ErrorCode.BUDGET_EXHAUSTED, message="Run LLM-call budget exhausted", retryable=False,
            ))

        # Stage delivery acknowledgement on a copy. An invalid finish must not
        # consume the stable outcome, mark answers delivered or replace the session.
        accepted = run.model_copy(deep=True)
        for work_request in accepted.work_requests:
            if work_request.status == WorkRequestStatus.STABLE:
                _transition_work_request(work_request, WorkRequestStatus.CONSUMED)
        self._merge_observed(accepted, result.observed_artifact_ids)
        self._mark_answers_delivered(accepted)
        if isinstance(result, ScientificCompletedResult):
            validation = self.gate.validate(accepted, result)
            if not validation.ok:
                run.completion_violations = list(validation.violations)
                return self._fail_run(run, ModuleError(
                    code=ErrorCode.CONTRACT_ERROR, message="Scientific completion was rejected", retryable=False,
                ))
        run = accepted

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
            run.scientific_session = result.session
            assert validation.report is not None
            try:
                rendered = self.report_renderer.render(validation.report)
                report = self.scheduler.artifact_registry.register_final_report(
                    rendered.candidate,
                    rendered.content,
                    run_id=run.run_id,
                )
            except Exception as error:
                return self._fail_run(run, ModuleError(
                    code=ErrorCode.ARTIFACT_MISSING,
                    message=f"Final report creation failed: {error}", retryable=False,
                ))
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
        return self._fail_run(run, result.error)

    def _fail_run(self, run: ResearchRun, error: ModuleError) -> ResearchRun:
        """Persist the terminal cause alongside the failed status."""
        run.status = RunStatus.FAILED
        run.terminal_error = error
        self._save(run)
        return run

    def _execute_work_request(self, run_id: str) -> ResearchRun:
        run = self.scheduler.store.load(run_id)
        active = self._active_work_request(run)
        if active is None:
            return self._fail_run(run, ModuleError(
                code=ErrorCode.CONTRACT_ERROR, message="No active work request to execute", retryable=False,
            ))

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

        # Only a NEW candidate needs task slots. Accepted work above must still
        # resume when its tasks have already filled the budget. Guard the Port
        # here so replacement compilers also avoid futile zero-slot calls.
        used_tasks = len(run.workflow.tasks) if run.workflow is not None else 0
        if used_tasks >= run.request.budget.max_tasks:
            error = ModuleError(
                code=ErrorCode.BUDGET_EXHAUSTED,
                message="No remaining task slots for a new work request",
                retryable=False,
            )
            _transition_work_request(active, WorkRequestStatus.FAILED, error=error)
            return self._fail_run(run, error)

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
            return self._fail_run(run, ModuleError(
                code=ErrorCode.BUDGET_EXHAUSTED, message="No remaining Compiler calls", retryable=False,
            ))

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
            # Only the invocation's error can authoritatively report its usage.
            usage_known = isinstance(error, CompilationError)
            if usage_known:
                run.llm_calls_used += error.llm_calls
            _transition_work_request(
                active,
                WorkRequestStatus.FAILED,
                error=ModuleError(
                    code=ErrorCode.CONTRACT_ERROR,
                    message=f"compilation failed: {error}",
                    retryable=False,
                    details={"compiler_usage_known": usage_known},
                ),
            )
            assert active.error is not None
            return self._fail_run(run, active.error)

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
            failure = ModuleError(
                code=ErrorCode.CONTRACT_ERROR,
                message=f"compiled workflow was rejected: {error}", retryable=False,
            )
            if active is not None:
                _transition_work_request(
                    active,
                    WorkRequestStatus.FAILED,
                    error=failure,
                )
            return self._fail_run(run, failure)
        run = self.scheduler.store.load(run_id)
        active = self._active_work_request(run)
        if active is None:
            return self._fail_run(run, ModuleError(
                code=ErrorCode.CONTRACT_ERROR, message="Accepted workflow lost its work request", retryable=False,
            ))
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
