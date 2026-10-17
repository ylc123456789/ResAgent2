"""Run lifecycle and Scientific invocations through the common Agent protocol."""

from __future__ import annotations

from datetime import UTC, datetime
from contextlib import contextmanager, nullcontext
from threading import Thread, Event
from contextvars import ContextVar
_OWNER = ContextVar("resagent2_run_owner", default=None)
from resagent2_runtime.cancellation import CancellationToken, OperationCancelled, bind_cancellation, current_cancellation, check_cancelled
from resagent2_runtime.lifecycle import model_boundary
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Protocol
from pydantic import BaseModel
from resagent2_components.literature import PreparedLiteratureImport, render_paper
from resagent2_runtime.budget import BudgetExhaustedError, DeadlineExceededError, execution_budget
from resagent2_contracts import (
    AgentOwner, AgentPermissions, AgentRequest, AgentResult, BusinessInput, TaskStatus, AttemptStatus, ArtifactImport, ConclusionRequirements,
    DatasetRef, ErrorCode, ModuleError, ModuleStatus,
    RecordedAnswer, ResearchRequest, RunStatus, ControlOrigin,
    SessionRef, SessionStatus, TaskBudget, UserAnswer,
    WorkRequestStatus, WorkspaceDescriptor,
    scientific_session_id,
)
from .compiler import CompilationError
from .completion import FinalReportRenderer, ScientificCompletionValidator
from .handoffs import read_json, receive_artifacts, receive_control, system_artifact
from .interpreter import WorkInterpreter
from .models import ResearchRun, RunInitialization, PendingModuleResult
from .ports import ModulePort
from .scheduler import _transition_work_request, _validate_answer
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
                 gate=None, report_renderer=None, dataset_ref_source=None, control_inbox=None):
        self.scientific_port = scientific_port
        self.compiler = compiler
        self.interpreter = interpreter
        self.scheduler = scheduler
        self.registry = registry
        self.gate = gate or ScientificCompletionValidator(registry, scheduler.artifact_registry)
        self.report_renderer = report_renderer or FinalReportRenderer()
        self.dataset_ref_source = dataset_ref_source
        self.control_inbox = control_inbox

    def create_run(self, run_id, request, *, literature=()):
        with self._execution(run_id):
            return self._create_owned(run_id, request, literature=literature)

    def _create_owned(self, run_id, request, *, literature=()):
        if self.scheduler.store.exists(run_id):
            raise ValueError("run already exists")
        now = datetime.now(UTC)
        request = ResearchRequest.model_validate(request)
        run = ResearchRun(run_id=run_id, request=request, status=RunStatus.RUNNING,
                          workspaces=self.scheduler._resolve_workspaces(run_id),
                          created_at=now, updated_at=now)
        run.initialization = RunInitialization(imports=request.input_artifacts,
                                               literature=[PreparedLiteratureImport.model_validate(item) for item in literature])
        self._save(run)
        with self._execution(run_id):
            self._complete_initialization(run)
            return self.run_until_stable(run_id)

    def _complete_initialization(self, run):
        """Recover original input positions before invoking any model."""
        progress = run.initialization
        for index in range(progress.next_import, len(progress.imports)):
            key = f"initialization:input:{index}"
            ref = self.scheduler.artifact_registry.register_import(progress.imports[index], run_id=run.run_id,
                                                                   operation_key=key, run=run)
            run.artifacts[ref.id] = ref
            progress.next_import = index + 1
            self._save(run)
            self.scheduler.artifact_registry.acknowledge_registration(key, run_id=run.run_id, run=run)
        self._import_literature(run)
        if run.conclusion_requirements_ref is None:
            run.conclusion_requirements_ref = system_artifact(
                self.scheduler.artifact_registry, run, "conclusion_requirements",
                ConclusionRequirements(required_evidence_kinds=run.request.required_evidence_kinds,
                                       required_artifacts=run.request.required_artifacts),
                operation_key="initialization:requirements",
            )
        progress.complete = True
        self._save(run)
        self.scheduler.artifact_registry.acknowledge_registration("initialization:requirements", run_id=run.run_id, run=run)

    def import_literature(self, run_id, literature):
        with self._execution(run_id):
            return self._import_owned(run_id, literature)

    def _import_owned(self, run_id, literature):
        run = self.scheduler.store.load(run_id)
        if run.status != RunStatus.PAUSED:
            raise ValueError("literature may only be appended to a paused Run")
        run.initialization.literature.extend(PreparedLiteratureImport.model_validate(item) for item in literature)
        run.initialization.complete = False
        self._save(run)
        self._complete_initialization(run)
        return run

    def _import_literature(self, run):
        progress = run.initialization
        with tempfile.TemporaryDirectory(prefix="resagent2-literature-") as temporary:
            paper_path = Path(temporary) / "paper.md"
            for index in range(progress.next_literature, len(progress.literature)):
                item = progress.literature[index]
                metadata = {"paper_key": item.paper.key, "paper": item.paper.model_dump(mode="json")}
                pdf_digest = None
                if item.pdf_path is not None:
                    with item.pdf_path.open("rb") as handle:
                        if handle.read(5) != b"%PDF-":
                            raise ValueError("imported file has no PDF signature")
                        handle.seek(0)
                        pdf_digest = hashlib.file_digest(handle, "sha256").hexdigest()
                    metadata["import_pdf_sha256"] = pdf_digest
                paper_path.write_text(render_paper(item.paper), encoding="utf-8")
                paper_key = f"initialization:paper:{index}"
                paper_ref = self.scheduler.artifact_registry.register_import(
                    ArtifactImport(uri=str(paper_path), kind="literature_paper", media_type="text/markdown",
                                   summary=item.paper.title, metadata=metadata), run_id=run.run_id,
                    operation_key=paper_key, run=run,
                )
                run.artifacts[paper_ref.id] = paper_ref
                if item.pdf_path is not None:
                    pdf_ref = self.scheduler.artifact_registry.register_import(
                        ArtifactImport(uri=str(item.pdf_path), kind="literature_pdf", media_type="application/pdf",
                                       summary=item.paper.title + " — original PDF", expected_sha256=pdf_digest,
                                       metadata={"paper_artifact_id": paper_ref.id, "source_artifact_id": paper_ref.id,
                                                 "source_url": item.paper.source_url}),
                        run_id=run.run_id, operation_key=f"initialization:pdf:{index}", run=run,
                    )
                    run.artifacts[pdf_ref.id] = pdf_ref
                progress.next_literature = index + 1
                self._save(run)
                for prefix in ("paper", "pdf"):
                    self.scheduler.artifact_registry.acknowledge_registration(
                        f"initialization:{prefix}:{index}", run_id=run.run_id, run=run,
                    )

    def answer_question(self, run_id, answer: UserAnswer):
        with self._execution(run_id):
            return self._answer_owned(run_id, answer)

    def _answer_owned(self, run_id, answer):
        run = self.scheduler.store.load(run_id)
        question = run.pending_question
        _validate_answer(question, answer)
        task_id = question.task_id
        session_id = question.session_id
        paired = RecordedAnswer(
            question_id=question.id, question_text=question.text, requested_fields=question.requested_fields,
            options=question.options, values=answer.values, answered_at=answer.answered_at,
            comment=answer.comment,
            run_id=run.run_id, session_id=session_id, task_id=task_id, attempt_number=question.attempt_number,
            action=question.action,
        )
        if task_id:
            task = self.scheduler._task(run, task_id)
            if not task.attempts or task.attempts[-1].number != question.attempt_number:
                raise ValueError("answer does not match the paused Attempt")
        run.answers.append(paired)
        self._settle_user_wait(run)
        run.pending_question = None
        run.status = RunStatus.RUNNING
        if task_id:
            self.scheduler.resume_task_in_place(run, task_id)
        self._save(run)
        return self.run_until_stable(run_id)

    def run_until_stable(self, run_id):
        with self._execution(run_id):
            try:
                return self._advance(run_id)
            except OperationCancelled as error:
                if not error.stopped:
                    run = self.scheduler.store.load(run_id)
                    return self._fail_run(run, ModuleError(code=ErrorCode.INTERRUPTED,
                        message="Unable to confirm execution stopped: " + str(error), retryable=False,
                        details={**error.details, "stop_confirmed": False}))
                return self._pause_interrupted(run_id, str(error))

    def _advance(self, run_id):
        while True:
            run = self.scheduler.store.load(run_id)
            if run.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
                return run
            interrupted = self._consume_controls(run)
            if interrupted:
                return self._pause_interrupted(run_id, "User interrupted current execution")
            if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.PAUSED}:
                return run
            if run.terminal_error and run.terminal_error.details.get("stop_confirmed") is False:
                return run
            if not run.initialization.complete:
                self._complete_initialization(run)
            self._merge_registered_datasets(run)
            if self.scheduler._recover_interrupted_attempts_in_place(run):
                self._save(run)
                continue
            if run.remaining_timeout_seconds(datetime.now(UTC)) <= 0:
                return self._fail_run(run, ModuleError(code=ErrorCode.TIMEOUT, message="Run execution-time budget exhausted", retryable=False))
            pending = run.pending_module_results.get("scientific")
            if pending is not None:
                recovered = self._apply_turn(run_id, pending.request, pending.result)
                if recovered.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.PAUSED}:
                    return recovered
                continue
            if run.llm_calls_used >= run.request.budget.max_llm_calls:
                return self._fail_run(run, ModuleError(code=ErrorCode.BUDGET_EXHAUSTED, message="Run LLM-call budget exhausted", retryable=False))
            active = self._active_work_request(run)
            if active and active.status not in {WorkRequestStatus.STABLE, WorkRequestStatus.FAILED, WorkRequestStatus.CANCELLED} and not run.parent_attention:
                result = self._execute_work_request(run_id)
                if result.status == RunStatus.PAUSED and current_cancellation() and current_cancellation().cancelled:
                    return self._pause_interrupted(run_id, current_cancellation().reason)
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
                return self._fail_run(run, ModuleError(code=code, message=str(error).strip() or type(error).__name__, retryable=False))
            with execution_budget(max_llm_calls=request.budget.max_llm_calls,
                                  timeout_seconds=run.remaining_timeout_seconds(datetime.now(UTC)),
                                  usage=RunUsagePort(run, self.scheduler.store)):
                check_cancelled()
                result = self.scientific_port.invoke(request)
            run = self._apply_turn(run_id, request, result)
            if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.PAUSED}:
                return run

    @contextmanager
    def _execution(self, run_id):
        """One owner writes Run/Session; the watcher reads inbox and signals only."""
        if _OWNER.get() == (id(self), run_id):
            yield
            return
        owner_marker = _OWNER.set((id(self), run_id))
        token = current_cancellation() or CancellationToken()
        stop = Event()
        thread = None
        lock = None
        if self.control_inbox is not None:
            # An OS lock fences separate CLI invocations as well as local threads.
            import fcntl
            lock_path = self.control_inbox.root / run_id / "owner.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock = lock_path.open("a")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock.close()
                _OWNER.reset(owner_marker)
                raise ValueError("Run already has an active execution owner")
            def watch():
                while not stop.wait(0.05):
                    if not self.scheduler.store.exists(run_id):
                        continue
                    run = self.scheduler.store.load(run_id)
                    if any(e.kind == "interrupt" for e in self.control_inbox.pending(run_id, run.consumed_control_ids)):
                        token.cancel("User interrupted current execution")
                        return
            thread = Thread(target=watch, daemon=True)
            thread.start()
        def boundary():
            if self.control_inbox is None:
                return False
            run = self.scheduler.store.load(run_id)
            return any(e.kind == "input" for e in self.control_inbox.pending(run_id, run.consumed_control_ids))
        try:
            with bind_cancellation(token), model_boundary(boundary):
                yield
        finally:
            stop.set()
            if thread:
                thread.join()
            if lock:
                lock.close()
            _OWNER.reset(owner_marker)

    def _consume_controls(self, run, *, records=None):
        if self.control_inbox is None:
            return False
        interrupted = False
        if records is None:
            records = self.control_inbox.pending(run.run_id, run.consumed_control_ids)
        for event in records:
            if event.text.strip():
                run.upstream_inputs.append(BusinessInput(event_id=event.event_id, text=event.text,
                                                        source="user", created_at=event.created_at))
                run.parent_attention = True
            interrupted |= event.kind == "interrupt"
            run.consumed_control_ids.append(event.event_id)
        self._save(run)
        return interrupted

    def resume_run(self, run_id):
        with self._execution(run_id):
            return self._resume_owned(run_id)

    def _resume_owned(self, run_id):
        run = self.scheduler.store.load(run_id)
        if run.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
            raise ValueError("terminal Run cannot resume")
        if self._consume_controls(run):
            return self._pause_interrupted(run_id, "User interrupted while the Run was waiting")
        if run.pending_question is not None:
            raise ValueError("answer the pending question before continuing")
        self._settle_user_wait(run)
        run.status = RunStatus.RUNNING
        self._save(run)
        return self.run_until_stable(run_id)

    @staticmethod
    def _settle_user_wait(run):
        """End one user wait without counting overlapping pause reasons twice."""
        started = run.user_wait_started_at
        if started is not None:
            run.user_wait_seconds += max(0, (datetime.now(UTC)-started).total_seconds())
        run.manually_paused_at = None

    def _cancel_work(self, run, work, reason):
        """No operations remain in flight here: invoked only after port return."""
        if work.status not in {WorkRequestStatus.REQUESTED, WorkRequestStatus.COMPILING, WorkRequestStatus.EXECUTING}:
            raise ValueError("delegation is not active")
        error = ModuleError(code=ErrorCode.INTERRUPTED, message=reason, retryable=False)
        for task in run.workflow.tasks if run.workflow else []:
            if task.work_request_id != work.id or task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.CANCELLED}:
                continue
            task.status = TaskStatus.CANCELLED
            if task.attempts:
                attempt = task.attempts[-1]
                if attempt.status in {AttemptStatus.RUNNING, AttemptStatus.NEEDS_USER_INPUT}:
                    attempt.status = AttemptStatus.CANCELLED
                    attempt.finished_at = datetime.now(UTC)
                    attempt.error = error
                    attempt.report = attempt.report or reason
                    if attempt.session:
                        terminate = getattr(self.scheduler.bindings[task.workflow_agent_kind].port, "terminate_session", None)
                        if terminate:
                            terminate(attempt.session.id, reason)
                        attempt.session = attempt.session.model_copy(update={"status": SessionStatus.CANCELLED})
                    self._save(run)
                    self.scheduler._persist_attempt_report(run, task, attempt)
        child_ids = {task.id for task in run.workflow.tasks if task.work_request_id == work.id} if run.workflow else set()
        cancelled_question_ids = {key for key, question in run.child_questions.items() if question.task_id in child_ids}
        if run.pending_question and run.pending_question.related_question_id in cancelled_question_ids:
            question = run.pending_question
            cancel = getattr(self.scientific_port, "cancel_question", None)
            if cancel:
                cancel(question.session_id, question.id, "Escalated question ended with its original child work")
            self._settle_user_wait(run)
            run.pending_question = None
        run.child_questions = {key: question for key, question in run.child_questions.items() if question.task_id not in child_ids}
        if run.pending_question is not None and run.pending_question.task_id in child_ids:
            self._settle_user_wait(run)
            run.pending_question = None
        run.pending_compilation = None
        _transition_work_request(work, WorkRequestStatus.CANCELLED, error=error)
        work.outcome = self.interpreter.interpret(work, tasks=run.workflow.tasks if run.workflow else [], artifacts=run.artifacts)
        for ref in work.outcome.artifacts:
            if ref.id not in run.scientific_artifact_ids:
                run.scientific_artifact_ids.append(ref.id)
        run.runtime_notices.append({"event_id": "cancel:" + work.id, "type": "work_interrupted",
            "work_request_id": work.id, "reason": reason, "stop_confirmed": True,
            "files_retained": True, "tasks": [t.model_dump(mode="json") for t in work.outcome.tasks],
            "artifact_ids": [ref.id for ref in work.outcome.artifacts],
            "file_retention_note": "Files were retained without rollback; partial files are not validated deliveries.",
            "reusable_workspace_ids": list(dict.fromkeys(t.workspace_id for t in run.workflow.tasks
                if t.work_request_id == work.id and t.workspace_id)) if run.workflow else []})
        run.parent_attention = True

    def _pause_interrupted(self, run_id, reason):
        run = self.scheduler.store.load(run_id)
        self._consume_controls(run)
        work = self._active_work_request(run)
        if work is not None and work.status in {WorkRequestStatus.REQUESTED, WorkRequestStatus.COMPILING, WorkRequestStatus.EXECUTING}:
            self._cancel_work(run, work, reason)
        run.status = RunStatus.PAUSED
        if run.manually_paused_at is None:
            run.manually_paused_at = datetime.now(UTC)
        run.runtime_notices.append({"event_id": "pause:" + str(len(run.runtime_notices)),
                                   "type": "user_interrupted", "reason": reason,
                                   "stop_confirmed": True, "files_retained": True})
        self._save(run)
        return run

    def _apply_parent_reply(self, run, signal):
        payload = signal.task_reply
        key = f"session:{signal.source_session_id}:turn:{signal.source_turn}:call:{signal.source_call_id}"
        if key in run.received_controls:
            return
        task_id = payload.get("task_id")
        question_id = payload.get("question_id")
        active = self._active_work_request(run)
        if task_id is not None:
            task = self.scheduler._task(run, task_id)
            if active is None or task.work_request_id != active.id or task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.CANCELLED}:
                raise ValueError("reply target is not this parent's live child")
            if signal.source_session_id != active.scientific_session_id:
                raise ValueError("reply source is not the direct parent")
            if question_id:
                question = run.child_questions.get(question_id)
                if question is None or question.task_id != task_id or question.recipient_session_id != signal.source_session_id:
                    raise ValueError("reply does not bind this child question")
                original = None
                if payload.get("user_answer_question_id"):
                    original = next((a for a in run.answers if a.question_id == payload["user_answer_question_id"] and a.source == "user" and a.action is None
                                     and a.session_id == signal.source_session_id and a.task_id is None), None)
                    if original is None:
                        raise ValueError("forwarded user answer is not authorized")
                answer = UserAnswer(question_id=question.id, values=original.values if original else payload["values"],
                                    answered_at=original.answered_at if original else datetime.now(UTC))
                _validate_answer(question, answer)
                run.answers.append(RecordedAnswer(**answer.model_dump(), question_text=question.text,
                    requested_fields=question.requested_fields, options=question.options,
                    run_id=run.run_id, session_id=question.session_id, task_id=task_id,
                    attempt_number=question.attempt_number, source="user" if original else "parent", source_session_id=signal.source_session_id,
                    original_question_id=original.question_id if original else None))
                del run.child_questions[question_id]
            if payload.get("user_answer_question_id") and not question_id:
                answer = next((a for a in run.answers if a.question_id == payload["user_answer_question_id"] and a.source == "user" and a.action is None
                               and a.session_id == signal.source_session_id and a.task_id is None), None)
                if answer is None:
                    raise ValueError("forwarded user answer is not authorized")
                records = run.task_inputs.setdefault(task_id, [])
                if not any(e.event_id == answer.question_id for e in records):
                    records.append(BusinessInput(event_id=answer.question_id, original_event_id=answer.question_id,
                        text=json.dumps({"question": answer.question_text, "values": answer.values}, ensure_ascii=False),
                        source="user", created_at=answer.answered_at))
            if payload.get("clarification"):
                run.task_inputs.setdefault(task_id, []).append(BusinessInput(event_id=key,
                    text=payload["clarification"], source="parent", created_at=datetime.now(UTC),
                    source_session_id=signal.source_session_id))
            if payload.get("user_input_event_id"):
                event = next((e for e in run.upstream_inputs if e.event_id == payload["user_input_event_id"] and e.source == "user"), None)
                if event is None:
                    raise ValueError("forwarded user record is not authorized")
                records = run.task_inputs.setdefault(task_id, [])
                if not any(e.event_id == event.event_id for e in records):
                    records.append(event.model_copy(update={"original_event_id": event.event_id}))
        run.received_controls[key] = task_id or "continue"
        # The parent may clarify several Tasks before releasing any child work.
        resume_work = payload.get("resume_work", True)
        if resume_work:
            for task in run.workflow.tasks if run.workflow else []:
                if active and task.work_request_id == active.id and task.status == TaskStatus.NEEDS_USER_INPUT:
                    if not any(q.task_id == task.id for q in run.child_questions.values()) and not (run.pending_question and run.pending_question.task_id == task.id):
                        self.scheduler.resume_task_in_place(run, task.id)
        run.parent_attention = not resume_work or bool(run.child_questions)

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
            for key in list(run.pending_registrations):
                if key.startswith("system:dataset_catalog:"):
                    self.scheduler.artifact_registry.acknowledge_registration(key, run_id=run.run_id, run=run)

    def _prepare_research_handoff(self, run):
        active = self._active_work_request(run)
        if active is None or active.status not in {WorkRequestStatus.STABLE, WorkRequestStatus.FAILED, WorkRequestStatus.CANCELLED}:
            return
        if active.outcome is None:
            result = self.interpreter.interpret(active, tasks=run.workflow.tasks if run.workflow else [], artifacts=run.artifacts)
            active.outcome = result
        for ref in active.outcome.artifacts:
            if ref.id not in run.scientific_artifact_ids:
                run.scientific_artifact_ids.append(ref.id)
        self._save(run)

    def _scientific_request(self, run):
        answers = [answer for answer in run.answers if answer.task_id is None and answer.session_id == run.scientific_session.id]
        fresh = [answer for answer in answers if answer.question_id not in run.delivered_answer_ids]
        results = [work.outcome for work in run.work_requests if work.outcome is not None]
        parent = run.scientific_session.id if run.scientific_session.status == SessionStatus.PAUSED or fresh or results or run.parent_attention or run.upstream_inputs else None
        controls = [key for key in run.received_controls if key.startswith(f"session:{run.scientific_session.id}:")]
        origin = None
        if controls:
            _, sid, _, turn, _, call_id = controls[-1].split(":", 5)
            origin = ControlOrigin(session_id=sid, turn=int(turn), call_id=call_id)
        return AgentRequest(
            run_id=run.run_id, agent=AgentOwner.SCIENTIFIC, instruction=_scientific_instruction(run.request),
            input_artifacts=self._authorized_artifacts(run), permissions=AgentPermissions(request_work=True),
            parent_session_id=parent, upstream_inputs=run.upstream_inputs,
            runtime_notices=run.runtime_notices,
            child_questions=list(run.child_questions.values()),
            release_control=run.parent_attention,
            user_answers=[answer for answer in answers if answer.action is None],
            resume_answers=fresh if parent else [], work_results=results, control_ack=origin if parent else None,
            task_context={"run_permissions": run.request.permissions.model_dump(
                mode="json", exclude={"schema_version"}),
                          "active_work_request_id": self._active_work_request(run).id if self._active_work_request(run) and self._active_work_request(run).status in {WorkRequestStatus.REQUESTED, WorkRequestStatus.COMPILING, WorkRequestStatus.EXECUTING} else None,
                          "child_tasks": [{"task_id": t.id, "instruction": t.instruction, "status": t.status.value}
                              for t in run.workflow.tasks if self._active_work_request(run) and t.work_request_id == self._active_work_request(run).id
                              and t.status in {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.NEEDS_USER_INPUT}] if run.workflow else [],
                          "conclusion_requirements": read_json(run.conclusion_requirements_ref),
                          "workspaces": [item.model_dump(mode="json", exclude_defaults=True) for item in self._workspace_descriptors(run)],
                          "dataset_refs": [ref.model_dump(mode="json") for ref in run.dataset_refs]},
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
            if "scientific" not in run.pending_module_results:
                run.pending_module_results["scientific"] = PendingModuleResult(request=request, result=result)
                self._save(run)
            if result.status in {ModuleStatus.COMPLETED, ModuleStatus.COMPLETED_WITH_WARNINGS} and self.control_inbox and self.control_inbox.pending(run_id, run.consumed_control_ids):
                # Completion is decided at receiving, after the last invocation.
                # An input submitted before this boundary must get a model turn.
                interrupted = self._defer_completed_turn(run, result)
                return self._pause_interrupted(run_id, "User interrupted before Run completion") if interrupted else run
            run.scientific_report = result.report
            if run.llm_calls_used > run.request.budget.max_llm_calls:
                raise ValueError("Run LLM-call budget exhausted")
            if result.session and (result.session.module != AgentOwner.SCIENTIFIC or result.session.id != run.scientific_session.id):
                raise ValueError("Scientific result has a foreign session")
            refs = receive_artifacts(self.scheduler.artifact_registry, run, request, result)
            accepted = run.model_copy(deep=True)
            for work in accepted.work_requests:
                if work.status == WorkRequestStatus.STABLE:
                    _transition_work_request(work, WorkRequestStatus.CONSUMED)
            accepted.delivered_answer_ids = list(dict.fromkeys([
                *accepted.delivered_answer_ids,
                *[answer.question_id for answer in request.resume_answers],
            ]))
            if result.status in {ModuleStatus.COMPLETED, ModuleStatus.COMPLETED_WITH_WARNINGS}:
                validation = self.gate.validate(accepted, result, refs)
                rendered = self.report_renderer.render(validation.report) if validation.ok else None
                # Validation/rendering may take time. Inputs accepted during
                # those steps must be processed before the Run becomes terminal.
                boundary = (self.control_inbox.settlement(run_id, accepted.consumed_control_ids)
                            if self.control_inbox else nullcontext([]))
                deferred = False
                interrupted = False
                with boundary as records:
                    if records:
                        interrupted = self._defer_completed_turn(accepted, result, refs=refs, records=records)
                        deferred = True
                    else:
                        if not validation.ok:
                            run.completion_violations = list(validation.violations)
                            raise ValueError("Scientific completion was rejected")
                        report = self.scheduler.artifact_registry.register_final_report(rendered.candidate, rendered.content, run_id=run.run_id, run=accepted)
                        accepted.artifacts[report.id] = report
                        accepted.final_opinion = validation.report.opinion
                        accepted.final_report_artifact_id = report.id
                        accepted.status = RunStatus.COMPLETED
                        accepted.scientific_session = result.session
                        accepted.scientific_artifact_ids = list(dict.fromkeys([
                            *accepted.scientific_artifact_ids, *[ref.id for ref in refs],
                        ]))
                        accepted.delivered_input_ids = list(dict.fromkeys([
                            *accepted.delivered_input_ids, *[e.event_id for e in request.upstream_inputs],
                        ]))
                        accepted.parent_attention = False
                        accepted.pending_module_results.pop("scientific", None)
                        # Keep the publication fence through the terminal commit.
                        self._save(accepted)
                self._acknowledge_scientific_outputs(accepted, result)
                if deferred:
                    return self._pause_interrupted(run_id, "User interrupted before Run completion") if interrupted else accepted
                self.scheduler.artifact_registry.acknowledge_registration("final_report", run_id=run.run_id, run=accepted)
                return accepted
            elif result.status == ModuleStatus.REQUEST_WORK:
                draft = result.control.work_request.request
                if not {key for task in draft.tasks for key in task.input_artifact_ids} <= {ref.id for ref in request.input_artifacts} | {ref.id for ref in refs}:
                    raise ValueError("work request references unauthorized materials")
                receive_control(accepted, request, result)
            elif result.status in {ModuleStatus.REPLY_TO_TASK, ModuleStatus.ABANDON_WORK}:
                if result.control.source_session_id != accepted.scientific_session.id:
                    raise ValueError("parent control source mismatch")
                if result.status == ModuleStatus.REPLY_TO_TASK:
                    self._apply_parent_reply(accepted, result.control)
                else:
                    work = self._active_work_request(accepted)
                    if work is None or work.id != result.control.abandon_work_id:
                        raise ValueError("cannot end a foreign delegation")
                    self._cancel_work(accepted, work, result.control.reason or "Parent ended delegation")
                    accepted.received_controls[f"session:{result.control.source_session_id}:turn:{result.control.source_turn}:call:{result.control.source_call_id}"] = work.id
            elif result.status in {ModuleStatus.YIELDED, ModuleStatus.INTERRUPTED}:
                accepted.scientific_session = result.session
                accepted.pending_module_results.pop("scientific", None)
                self._save(accepted)
                if result.status == ModuleStatus.INTERRUPTED:
                    return self._pause_interrupted(run_id, result.report)
                return accepted
            elif result.status == ModuleStatus.NEEDS_USER_INPUT:
                receive_control(accepted, request, result)
                accepted.status = RunStatus.PAUSED
            else:
                return self._fail_run(run, result.error)
            accepted.scientific_session = result.session or accepted.scientific_session
            for ref in refs:
                if ref.id not in accepted.scientific_artifact_ids:
                    accepted.scientific_artifact_ids.append(ref.id)
            accepted.delivered_input_ids = list(dict.fromkeys([*accepted.delivered_input_ids,
                                                               *[e.event_id for e in request.upstream_inputs]]))
            if result.status not in {ModuleStatus.ABANDON_WORK, ModuleStatus.REPLY_TO_TASK, ModuleStatus.NEEDS_USER_INPUT}:
                accepted.parent_attention = False
            accepted.pending_module_results.pop("scientific", None)
            self._save(accepted)
            for key in list(accepted.pending_registrations):
                if key.startswith(f"session:{result.session.id}:result:"):
                    self.scheduler.artifact_registry.acknowledge_registration(key, run_id=run.run_id, run=accepted)
            if accepted.status == RunStatus.COMPLETED:
                self.scheduler.artifact_registry.acknowledge_registration("final_report", run_id=run.run_id, run=accepted)
            return accepted
        except Exception as error:
            # Registration may already have saved an allocated/frozen snapshot
            # from the accepted copy. Keep those durable facts on failure.
            latest = self.scheduler.store.load(run_id)
            latest.scientific_report = run.scientific_report
            latest.completion_violations = run.completion_violations
            return self._fail_run(latest, ModuleError(code=ErrorCode.CONTRACT_ERROR, message=str(error).strip() or type(error).__name__, retryable=False))

    def _defer_completed_turn(self, run, result, *, refs=(), records=None):
        interrupted = self._consume_controls(run, records=records)
        defer = getattr(self.scientific_port, "defer_completion", None)
        if defer is None or result.session is None:
            raise ValueError("port cannot defer completion for pending upstream input")
        defer(result.session.id, "Run completion deferred: upstream input arrived before the owner committed completion; process it before finishing")
        run.scientific_session = result.session.model_copy(update={"status": SessionStatus.PAUSED})
        run.scientific_artifact_ids = list(dict.fromkeys([
            *run.scientific_artifact_ids, *[ref.id for ref in refs],
        ]))
        run.pending_module_results.pop("scientific", None)
        run.parent_attention = True
        self._save(run)
        return interrupted

    def _acknowledge_scientific_outputs(self, run, result):
        for key in list(run.pending_registrations):
            if key.startswith(f"session:{result.session.id}:result:"):
                self.scheduler.artifact_registry.acknowledge_registration(key, run_id=run.run_id, run=run)

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
            run.pending_compilation = None
            self._save(run)
            return self.scheduler.run_until_stable(run_id)
        try:
            if (len(run.workflow.tasks) if run.workflow else 0) >= run.request.execution_limits.max_tasks:
                failure = ModuleError(code=ErrorCode.BUDGET_EXHAUSTED, message="No remaining task slots", retryable=False)
                _transition_work_request(active, WorkRequestStatus.FAILED, error=failure)
                run.pending_compilation = None
                self._save(run)
                return run
            if active.status == WorkRequestStatus.REQUESTED:
                _transition_work_request(active, WorkRequestStatus.COMPILING)
                self._save(run)
            if run.pending_compilation is None:
                with execution_budget(max_llm_calls=run.request.budget.max_llm_calls-run.llm_calls_used,
                                      timeout_seconds=run.remaining_timeout_seconds(datetime.now(UTC)),
                                      usage=RunUsagePort(run, self.scheduler.store)):
                    compilation = self.compiler.compile(active, current=run.workflow, registry=self.registry,
                                                        limits=run.request.execution_limits,
                                                        workspaces=self._workspace_descriptors(run))
                check_cancelled()
                run.pending_compilation = compilation.output
                self._save(run)
            if run.llm_calls_used > run.request.budget.max_llm_calls:
                raise ValueError("Compiler exceeded Run budget")
            if run.workflow is None:
                self.scheduler.accept_proposal(run_id, run.pending_compilation)
            else:
                self.scheduler.apply_patch(run_id, run.pending_compilation)
            run = self.scheduler.store.load(run_id)
            active = self._active_work_request(run)
            _transition_work_request(active, WorkRequestStatus.EXECUTING, workflow_revision=run.workflow.revision)
            run.pending_compilation = None
            self._save(run)
            return self.scheduler.run_until_stable(run_id)
        except OperationCancelled:
            raise
        except Exception as error:
            run = self.scheduler.store.load(run_id)
            active = self._active_work_request(run)
            code = (ErrorCode.TIMEOUT if isinstance(error, DeadlineExceededError) else
                    ErrorCode.BUDGET_EXHAUSTED if isinstance(error, BudgetExhaustedError) else ErrorCode.CONTRACT_ERROR)
            failure = ModuleError(code=code, message=str(error).strip() or type(error).__name__, retryable=False,
                                  details={"compiler_usage_known": isinstance(error, CompilationError)})
            if active:
                _transition_work_request(active, WorkRequestStatus.FAILED, error=failure)
            run.pending_compilation = None
            if code in {ErrorCode.TIMEOUT, ErrorCode.BUDGET_EXHAUSTED}:
                return self._fail_run(run, failure)
            self._save(run)
            return run

    @staticmethod
    def _active_work_request(run):
        return next((work for work in run.work_requests if work.status in {
            WorkRequestStatus.REQUESTED, WorkRequestStatus.COMPILING, WorkRequestStatus.EXECUTING, WorkRequestStatus.STABLE,
        } or (work.status == WorkRequestStatus.FAILED and work.outcome is None)), None)

    def _authorized_artifacts(self, run):
        imports = [ref.id for ref in run.artifacts.values() if ref.metadata.get("source_type") == "import"]
        system = [ref.id for ref in (run.conclusion_requirements_ref, run.dataset_catalog_ref) if ref is not None]
        ids = list(dict.fromkeys([*imports, *system, *run.scientific_artifact_ids]))
        return [run.artifacts[key] for key in ids]

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
