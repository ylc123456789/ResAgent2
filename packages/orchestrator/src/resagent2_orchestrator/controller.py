"""Run lifecycle and Scientific invocations through the common Agent protocol."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from pydantic import BaseModel
from resagent2_runtime.budget import BudgetExhaustedError, DeadlineExceededError, execution_budget
from resagent2_contracts import (
    AgentOwner, AgentPermissions, AgentRequest, AgentResult, ConclusionRequirements,
    DatasetRef, ErrorCode, ModuleError, ModuleStatus, ObservationTrace, PendingQuestion,
    QuestionDraft, RecordedAnswer, ResearchRequest, ResearchIndex, RunStatus, ScientificAssessment,
    SessionRef, SessionStatus, TaskBudget, UserAnswer, WorkFeedback, WorkRequest,
    WorkRequestDraft, WorkRequestStatus, WorkTaskOutcome, WorkspaceDescriptor, WorkRecord, WorkAttemptRecord,
    scientific_session_id,
)
from .compiler import CompilationError
from .completion import FinalReportRenderer, ScientificCompletionValidator
from .handoffs import read_json, receive_artifacts, system_artifact
from .interpreter import WorkInterpreter, build_research_index, validate_brief
from .models import ResearchRun
from .ports import ModulePort
from .scheduler import _question_id, _transition_work_request, _validate_answer
from .usage import RunUsagePort


class ScientificGate(Protocol):
    """Validate a Scientific completion against persisted Run evidence."""
    def validate(self, run, result, refs): ...


class DatasetRefSource(Protocol):
    """Discover currently registered dataset references."""
    def references(self) -> list[DatasetRef]: ...


def _scientific_instruction(request: ResearchRequest) -> str:
    parts = [request.goal]
    if request.hypothesis:
        parts.append("Hypothesis: " + request.hypothesis)
    if request.context:
        parts.append(request.context)
    parts.extend(request.constraints)
    return "\n\n".join(parts)


class ResearchController:
    """Coordinate Scientific reasoning, execution feedback and user pauses."""
    def __init__(self, *, scientific_port: ModulePort, compiler, scheduler, registry, interpreter: WorkInterpreter,
                 gate=None, report_renderer=None, dataset_ref_source=None):
        self.scientific_port = scientific_port
        self.compiler = compiler
        self.interpreter = interpreter
        self.scheduler = scheduler
        self.registry = registry
        self.gate = gate or ScientificCompletionValidator(registry)
        self.report_renderer = report_renderer or FinalReportRenderer()
        self.dataset_ref_source = dataset_ref_source

    def create_run(self, run_id, request):
        if self.scheduler.store.exists(run_id):
            raise ValueError("run already exists")
        now = datetime.now(UTC)
        request = ResearchRequest.model_validate(request)
        run = ResearchRun(run_id=run_id, request=request, status=RunStatus.RUNNING,
                          workspaces=self.scheduler._resolve_workspaces(run_id),
                          created_at=now, updated_at=now)
        for item in request.input_artifacts:
            ref = self.scheduler.artifact_registry.register_import(item, run_id=run_id)
            run.artifacts[ref.id] = ref
        run.conclusion_requirements_ref = system_artifact(
            self.scheduler.artifact_registry, run, "conclusion_requirements",
            ConclusionRequirements(required_evidence_kinds=request.required_evidence_kinds),
        )
        self._save(run)
        return self.run_until_stable(run_id)

    def answer_question(self, run_id, answer: UserAnswer):
        run = self.scheduler.store.load(run_id)
        question = run.pending_question
        _validate_answer(question, answer)
        task_id = question.task_id
        session_id = None if task_id else run.scientific_session.id
        paired = RecordedAnswer(
            question_id=question.id, question_text=question.text, requested_fields=question.requested_fields,
            options=question.options, values=answer.values, answered_at=answer.answered_at,
            run_id=run.run_id, session_id=session_id, task_id=task_id, attempt_number=question.attempt_number,
            action=question.action,
        )
        if task_id:
            task = self.scheduler._task(run, task_id)
            if not task.attempts or task.attempts[-1].number != question.attempt_number:
                raise ValueError("answer does not match the paused Attempt")
        system_artifact(self.scheduler.artifact_registry, run, "answer", paired,
                        session_id=session_id, task_id=task_id, attempt_number=question.attempt_number)
        run.answers.append(paired)
        run.user_wait_seconds += max(0, (datetime.now(UTC)-question.created_at).total_seconds())
        run.pending_question = None
        run.pending_question_ref = None
        run.status = RunStatus.RUNNING
        if task_id:
            self.scheduler.resume_task_in_place(run, task_id)
        self._save(run)
        return self.run_until_stable(run_id)

    def run_until_stable(self, run_id):
        while True:
            run = self.scheduler.store.load(run_id)
            if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.PAUSED}:
                return run
            self._merge_registered_datasets(run)
            if self.scheduler._recover_interrupted_attempts_in_place(run):
                self._save(run)
                continue
            if run.remaining_timeout_seconds(datetime.now(UTC)) <= 0:
                return self._fail_run(run, ModuleError(code=ErrorCode.TIMEOUT, message="Run execution-time budget exhausted", retryable=False))
            if run.llm_calls_used >= run.request.budget.max_llm_calls:
                return self._fail_run(run, ModuleError(code=ErrorCode.BUDGET_EXHAUSTED, message="Run LLM-call budget exhausted", retryable=False))
            active = self._active_work_request(run)
            if active and active.status != WorkRequestStatus.STABLE:
                self._execute_work_request(run_id)
                continue
            self._bind_initial_scientific_session(run)
            try:
                self._prepare_research_handoff(run)
                if run.remaining_timeout_seconds(datetime.now(UTC)) <= 0:
                    raise DeadlineExceededError("Run execution-time budget exhausted")
                if run.llm_calls_used >= run.request.budget.max_llm_calls:
                    raise BudgetExhaustedError("Run LLM-call budget exhausted")
                request = self._scientific_request(run)
                self._save(run)
            except Exception as error:
                run = self.scheduler.store.load(run_id)
                code = (ErrorCode.TIMEOUT if isinstance(error, DeadlineExceededError) else
                        ErrorCode.BUDGET_EXHAUSTED if isinstance(error, BudgetExhaustedError) else ErrorCode.CONTRACT_ERROR)
                return self._fail_run(run, ModuleError(code=code, message=str(error) or type(error).__name__, retryable=False))
            with execution_budget(max_llm_calls=request.budget.max_llm_calls,
                                  timeout_seconds=run.remaining_timeout_seconds(datetime.now(UTC)),
                                  usage=RunUsagePort(run, self.scheduler.store)):
                result = self.scientific_port.invoke(request)
            run = self._apply_turn(run_id, request, result)
            if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.PAUSED}:
                return run

    def _merge_registered_datasets(self, run):
        if self.dataset_ref_source is None:
            return
        by_id = {ref.dataset_id: ref for ref in run.dataset_refs}
        for ref in self.dataset_ref_source.references():
            if ref.dataset_id in by_id and by_id[ref.dataset_id] != ref:
                raise ValueError("dataset remapped during Run")
            by_id[ref.dataset_id] = ref
        if list(by_id.values()) != run.dataset_refs or run.dataset_catalog_ref is None:
            run.dataset_refs = list(by_id.values())
            run.dataset_catalog_ref = system_artifact(self.scheduler.artifact_registry, run, "dataset_catalog",
                                                     {"datasets": [ref.model_dump(mode="json") for ref in run.dataset_refs]})
            self._save(run)

    def _research_index(self, run):
        index = build_research_index(
            run_id=run.run_id, artifacts=self._authorized_artifacts(run),
            work_requests=run.work_requests, tasks=run.workflow.tasks if run.workflow else (),
        )
        if run.research_index_ref and read_json(run.research_index_ref, ResearchIndex) == index:
            return index, run.research_index_ref
        return index, system_artifact(self.scheduler.artifact_registry, run, "research_index", index)

    def _prepare_research_handoff(self, run):
        """Persist one complete handoff; replay never regenerates an accepted brief."""
        active = self._active_work_request(run)
        if active is None or active.status != WorkRequestStatus.STABLE:
            _, run.research_index_ref = self._research_index(run)
            self._save(run)
            return
        if active.id in run.feedback_refs:
            # An interrupted Scientific turn may already have registered new materials.
            # Refresh navigation while reusing the committed brief and its original sources.
            _, run.research_index_ref = self._research_index(run)
            self._save(run)
            return
        record = WorkRecord(
            run_id=run.run_id, work_request_id=active.id,
            session_id=active.scientific_session_id, previous_work_request=active.request,
            work_outcome=active.outcome, unresolved_task_outcomes=self._unresolved_tasks(run),
            attempts=[WorkAttemptRecord(
                task_id=task.id, attempt_number=attempt.number, status=attempt.status,
                summary=attempt.report, artifact_ids=attempt.artifact_ids, error=attempt.error,
            ) for task in run.workflow.tasks if task.work_request_id == active.id for attempt in task.attempts],
        )
        record_ref = system_artifact(self.scheduler.artifact_registry, run, "work_record", record,
                                     session_id=active.scientific_session_id)
        index, index_ref = self._research_index(run)
        with execution_budget(max_llm_calls=run.request.budget.max_llm_calls-run.llm_calls_used,
                              timeout_seconds=run.remaining_timeout_seconds(datetime.now(UTC)),
                              usage=RunUsagePort(run, self.scheduler.store)):
            brief = self.interpreter.interpret(record_ref=record_ref, index=index,
                                               artifacts=self._authorized_artifacts(run))
        # This boundary also checks injected implementations, not only the production LLM.
        validate_brief(brief, {entry.artifact_id for group in index.groups for entry in group.artifacts})
        feedback = WorkFeedback(
            run_id=run.run_id, work_request_id=active.id, session_id=active.scientific_session_id,
            work_record_artifact_id=record_ref.id, index_artifact_id=index_ref.id,
            brief=brief,
        )
        ref = system_artifact(self.scheduler.artifact_registry, run, "work_feedback", feedback,
                              session_id=active.scientific_session_id)
        run.feedback_refs[active.id] = ref
        run.research_index_ref = index_ref
        self._save(run)

    def _scientific_request(self, run):
        active = self._active_work_request(run)
        resume_refs = []
        if active and active.status == WorkRequestStatus.STABLE:
            resume_refs.append(run.feedback_refs[active.id])
        else:
            pending = {answer.question_id for answer in self._pending_answers(run)}
            for ref in run.artifacts.values():
                if ref.kind == "answer" and ref.session_id == run.scientific_session.id:
                    answer = read_json(ref, RecordedAnswer)
                    if answer.question_id in pending:
                        resume_refs.append(ref)
        parent = run.scientific_session.id if run.scientific_session.status == SessionStatus.PAUSED or resume_refs else None
        refs = self._authorized_artifacts(run)
        return AgentRequest(
            run_id=run.run_id, agent=AgentOwner.SCIENTIFIC, instruction=_scientific_instruction(run.request),
            input_artifacts=refs, permissions=AgentPermissions(request_work=True),
            parent_session_id=parent, resume_artifact_ids=[ref.id for ref in resume_refs],
            budget=TaskBudget(max_llm_calls=run.request.budget.max_llm_calls-run.llm_calls_used,
                              timeout_seconds=max(1, int(run.remaining_timeout_seconds(datetime.now(UTC))))),
        )

    def _bind_initial_scientific_session(self, run):
        if run.scientific_session:
            return
        now = datetime.now(UTC)
        sid = scientific_session_id(run.run_id)
        run.scientific_session = SessionRef(id=sid, module=AgentOwner.SCIENTIFIC, state_uri=f"session://{sid}",
                                            status=SessionStatus.ACTIVE, created_at=now, updated_at=now)
        self._save(run)

    def _apply_turn(self, run_id, request, raw_result):
        run = self.scheduler.store.load(run_id)
        raw = raw_result.model_dump() if isinstance(raw_result, BaseModel) else raw_result
        try:
            result = AgentResult.model_validate(raw)
            run.scientific_report = result.report
            if run.llm_calls_used > run.request.budget.max_llm_calls:
                raise ValueError("Run LLM-call budget exhausted")
            if result.session and (result.session.module != AgentOwner.SCIENTIFIC or result.session.id != run.scientific_session.id):
                raise ValueError("Scientific result has a foreign session")
            refs, control_ref = receive_artifacts(self.scheduler.artifact_registry, run, request, result)
            traces = [ref for ref in refs if ref.kind == "observation_trace"]
            observed = read_json(traces[0], ObservationTrace).observed_artifact_ids if len(traces) == 1 else []
            if not set(observed) <= {ref.id for ref in request.input_artifacts} | {ref.id for ref in refs}:
                raise ValueError("observations reference unknown artifacts")
            accepted = run.model_copy(deep=True)
            accepted.scientific_observed_artifact_ids = sorted(set(accepted.scientific_observed_artifact_ids) | set(observed))
            for work in accepted.work_requests:
                if work.status == WorkRequestStatus.STABLE:
                    _transition_work_request(work, WorkRequestStatus.CONSUMED)
            accepted.delivered_answer_ids = list(dict.fromkeys([
                *accepted.delivered_answer_ids,
                *[read_json(ref)["question_id"] for ref in request.input_artifacts
                  if ref.id in request.resume_artifact_ids and ref.kind == "answer"],
            ]))
            if result.status in {ModuleStatus.COMPLETED, ModuleStatus.COMPLETED_WITH_WARNINGS}:
                validation = self.gate.validate(accepted, result, refs)
                if not validation.ok:
                    run.completion_violations = list(validation.violations)
                    raise ValueError("Scientific completion was rejected")
                rendered = self.report_renderer.render(validation.report)
                report = self.scheduler.artifact_registry.register_final_report(rendered.candidate, rendered.content, run_id=run.run_id)
                accepted.artifacts[report.id] = report
                accepted.final_opinion = validation.report.opinion
                accepted.final_report_artifact_id = report.id
                accepted.status = RunStatus.COMPLETED
            elif result.status == ModuleStatus.REQUEST_WORK:
                data = read_json(control_ref)
                draft = WorkRequestDraft.model_validate(data["work_request"])
                if not set(draft.input_artifact_ids) <= {ref.id for ref in request.input_artifacts} | {ref.id for ref in refs}:
                    raise ValueError("work request references unauthorized materials")
                accepted.latest_scientific_assessment = ScientificAssessment.model_validate(data["assessment"])
                now = datetime.now(UTC)
                accepted.work_requests.append(WorkRequest(id=f"work_{len(accepted.work_requests)+1}", run_id=run.run_id,
                                                         scientific_session_id=result.session.id, request=draft,
                                                         created_at=now, updated_at=now))
            elif result.status == ModuleStatus.NEEDS_USER_INPUT:
                draft = read_json(control_ref, QuestionDraft)
                assessments = [ref for ref in refs if ref.kind == "scientific_assessment"]
                if len(assessments) == 1:
                    accepted.latest_scientific_assessment = read_json(assessments[0], ScientificAssessment)
                accepted.pending_question = PendingQuestion(id=_question_id(), run_id=run.run_id, text=draft.text,
                                                           requested_fields=draft.requested_fields, options=draft.options, created_at=datetime.now(UTC), action=draft.action)
                accepted.pending_question_ref = control_ref
                accepted.status = RunStatus.PAUSED
            else:
                return self._fail_run(run, result.error)
            accepted.scientific_session = result.session or accepted.scientific_session
            if accepted.status == RunStatus.COMPLETED:
                _, accepted.research_index_ref = self._research_index(accepted)
            self._save(accepted)
            return accepted
        except Exception as error:
            return self._fail_run(run, ModuleError(code=ErrorCode.CONTRACT_ERROR, message=str(error) or type(error).__name__, retryable=False))

    def _fail_run(self, run, error):
        run.status = RunStatus.FAILED
        run.terminal_error = error
        if not run.scientific_report:
            run.scientific_report = error.message
        self._save(run)
        return run

    def _execute_work_request(self, run_id):
        run = self.scheduler.store.load(run_id)
        active = self._active_work_request(run)
        if active is None:
            raise ValueError("no active work request")
        if active.status == WorkRequestStatus.EXECUTING:
            return self.scheduler.run_until_stable(run_id)
        if active.status == WorkRequestStatus.COMPILING and run.workflow and any(t.work_request_id == active.id for t in run.workflow.tasks):
            _transition_work_request(active, WorkRequestStatus.EXECUTING, workflow_revision=run.workflow.revision)
            self._save(run)
            return self.scheduler.run_until_stable(run_id)
        try:
            if (len(run.workflow.tasks) if run.workflow else 0) >= run.request.execution_limits.max_tasks:
                failure = ModuleError(code=ErrorCode.BUDGET_EXHAUSTED, message="No remaining task slots", retryable=False)
                _transition_work_request(active, WorkRequestStatus.FAILED, error=failure)
                return self._fail_run(run, failure)
            if active.status == WorkRequestStatus.REQUESTED:
                _transition_work_request(active, WorkRequestStatus.COMPILING)
                self._save(run)
            with execution_budget(max_llm_calls=run.request.budget.max_llm_calls-run.llm_calls_used,
                                  timeout_seconds=run.remaining_timeout_seconds(datetime.now(UTC)),
                                  usage=RunUsagePort(run, self.scheduler.store)):
                compilation = self.compiler.compile(active, current=run.workflow, registry=self.registry,
                                                    limits=run.request.execution_limits,
                                                    workspaces=self._workspace_descriptors(run))
            self._save(run)
            if run.llm_calls_used > run.request.budget.max_llm_calls:
                raise ValueError("Compiler exceeded Run budget")
            if run.workflow is None:
                self.scheduler.accept_proposal(run_id, compilation.output)
            else:
                self.scheduler.apply_patch(run_id, compilation.output)
            run = self.scheduler.store.load(run_id)
            active = self._active_work_request(run)
            _transition_work_request(active, WorkRequestStatus.EXECUTING, workflow_revision=run.workflow.revision)
            self._save(run)
            return self.scheduler.run_until_stable(run_id)
        except Exception as error:
            run = self.scheduler.store.load(run_id)
            active = self._active_work_request(run)
            code = (ErrorCode.TIMEOUT if isinstance(error, DeadlineExceededError) else
                    ErrorCode.BUDGET_EXHAUSTED if isinstance(error, BudgetExhaustedError) else ErrorCode.CONTRACT_ERROR)
            failure = ModuleError(code=code, message=str(error) or type(error).__name__, retryable=False,
                                  details={"compiler_usage_known": isinstance(error, CompilationError)})
            if active:
                _transition_work_request(active, WorkRequestStatus.FAILED, error=failure)
            return self._fail_run(run, failure)

    @staticmethod
    def _active_work_request(run):
        return next((work for work in run.work_requests if work.status in {
            WorkRequestStatus.REQUESTED, WorkRequestStatus.COMPILING, WorkRequestStatus.EXECUTING, WorkRequestStatus.STABLE,
        }), None)

    def _pending_answers(self, run):
        return [answer for answer in run.answers if answer.task_id is None and answer.question_id not in run.delivered_answer_ids]

    def _authorized_artifacts(self, run):
        current = {ref.id for ref in run.feedback_refs.values()}
        refs = [ref for ref in run.artifacts.values()
                if ref.kind not in {"work_feedback", "dataset_catalog", "conclusion_requirements", "acceptance_requirements"}
                or ref.id in current
                or ref == run.dataset_catalog_ref or ref == run.conclusion_requirements_ref]
        # Historical snapshots remain readable. The final index ref is the current
        # reading entry, without adding a second domain field to AgentRequest.
        return sorted(refs, key=lambda ref: ref == run.research_index_ref)

    def _unresolved_tasks(self, run):
        if not run.workflow:
            return []
        result = []
        for work in run.work_requests:
            if work.outcome:
                result.extend(item for item in work.outcome.tasks if item.status != "completed")
        return result

    def _workspace_descriptors(self, run):
        return [WorkspaceDescriptor(workspace_id=key, source_kind=record.source.source_kind)
                for key, record in run.workspaces.items()]

    def _save(self, run):
        run.updated_at = datetime.now(UTC)
        self.scheduler.store.save(ResearchRun.model_validate(run.model_dump()))
