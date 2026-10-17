"""Deterministic scheduling of single-mode Agent invocations."""

from __future__ import annotations

from datetime import UTC, datetime
from graphlib import TopologicalSorter
from pathlib import Path
from pydantic import BaseModel
from resagent2_runtime.budget import execution_budget
from resagent2_runtime.cancellation import OperationCancelled, check_cancelled
from resagent2_contracts import (
    AgentOwner, AgentPermissions, AgentRequest, AgentResult, Attempt, AttemptStatus,
    ErrorCode, ModuleError, ModuleStatus, RunStatus, ArtifactCandidate,
    TaskAcceptanceSpec, TaskBudget, TaskStatus, Workflow, WorkflowAgentKind,
    WorkflowPatch, WorkflowProposal, WorkflowTask, WorkRequestStatus,
    ControlOrigin, WorkspaceGrant, WorkspaceRecord, WorkspaceSourceKind,
)
from .artifacts import ArtifactRegistrationError, ArtifactRegistry
from .handoffs import check_acceptance, read_json, receive_artifacts, receive_control, system_artifact
from .layout import RunLayout
from .models import PendingModuleResult
from .store import InMemoryRunStore
from .usage import RunUsagePort
from .workflow_validation import validate_workflow_candidate


class OrchestrationError(ValueError):
    """A workflow transition or Agent result violates an execution invariant."""


def _validate_answer(question, answer):
    if question is None or answer.question_id != question.id:
        raise OrchestrationError("answer does not match pending question")
    if set(question.requested_fields) != set(answer.values):
        raise OrchestrationError("answer is missing requested fields")
    if answer.comment is not None and question.action is None:
        raise OrchestrationError("operation comments require an approval question; answer business questions in the requested fields")


_WORK_REQUEST_TRANSITIONS = {
    WorkRequestStatus.REQUESTED: {WorkRequestStatus.COMPILING, WorkRequestStatus.FAILED, WorkRequestStatus.CANCELLED},
    WorkRequestStatus.COMPILING: {WorkRequestStatus.EXECUTING, WorkRequestStatus.FAILED, WorkRequestStatus.CANCELLED},
    WorkRequestStatus.EXECUTING: {WorkRequestStatus.STABLE, WorkRequestStatus.FAILED, WorkRequestStatus.CANCELLED},
    WorkRequestStatus.STABLE: {WorkRequestStatus.CONSUMED, WorkRequestStatus.FAILED},
    WorkRequestStatus.CONSUMED: set(), WorkRequestStatus.FAILED: set(), WorkRequestStatus.CANCELLED: set(),
}


def _transition_work_request(work_request, status, *, workflow_revision=None, outcome=None, error=None):
    if status not in _WORK_REQUEST_TRANSITIONS[work_request.status]:
        raise OrchestrationError(f"illegal work request transition: {work_request.status} -> {status}")
    work_request.status = status
    work_request.updated_at = datetime.now(UTC)
    if workflow_revision is not None:
        work_request.workflow_revision = workflow_revision
    if outcome is not None:
        work_request.outcome = outcome
    if error is not None:
        work_request.error = error


class WorkflowScheduler:
    """Dispatch ready tasks and persist attempts, requirements and outputs."""
    def __init__(self, *, bindings, store=None, artifact_root=".resagent2/artifacts", data_root=None, workspaces=None):
        self.bindings = dict(bindings)
        self.store = store or InMemoryRunStore()
        self.artifact_registry = ArtifactRegistry(artifact_root, self.store)
        self.run_layout = RunLayout(data_root) if data_root else RunLayout.from_env()
        self.workspace_specs = dict(workspaces or {})

    def accept_proposal(self, run_id, proposal):
        run = self.store.load(run_id)
        if run.workflow is not None:
            raise OrchestrationError("run already has an accepted workflow")
        proposal = WorkflowProposal.model_validate(proposal.model_dump())
        validate_workflow_candidate(proposal)
        if len(proposal.tasks) > run.request.execution_limits.max_tasks:
            raise OrchestrationError("workflow exceeds run task limit")
        tasks = self._tasks_from_proposal(run, proposal.tasks)
        run.workflow = Workflow(run_id=run_id, revision=1, tasks=tasks, created_from=proposal.work_request_id)
        self._save(run)
        self._acknowledge_acceptance_registrations(run)
        return run.model_copy(deep=True)

    def _acknowledge_acceptance_registrations(self, run):
        accepted = {task.acceptance_ref.id for task in run.workflow.tasks if task.acceptance_ref} if run.workflow else set()
        keys = [key for key, pending in run.pending_registrations.items()
                if pending.ref.kind == "acceptance_requirements" and pending.ref.id in accepted]
        for key in keys:
            self.artifact_registry.acknowledge_registration(key, run_id=run.run_id, run=run)
        return bool(keys)

    def _tasks_from_proposal(self, run, proposals):
        self._require_bindings(item.workflow_agent_kind for item in proposals)
        tasks = []
        for item in proposals:
            if not set(item.input_artifacts) <= set(run.artifacts):
                raise OrchestrationError("task input references unknown artifacts")
            spec = item.acceptance_spec or TaskAcceptanceSpec()
            spec = spec.model_copy(update={"required_output_names": list(dict.fromkeys([*spec.required_output_names, *item.output_names]))})
            ref = None
            if any(spec.model_dump(exclude={"schema_version"}).values()):
                ref = system_artifact(self.artifact_registry, run, "acceptance_requirements", spec, task_id=item.id)
            tasks.append(WorkflowTask(
                id=item.id, work_request_id=item.work_request_id,
                workflow_agent_kind=item.workflow_agent_kind, instruction=item.instruction,
                shared_goal=item.shared_goal, shared_constraints=item.shared_constraints,
                depends_on=item.depends_on, workspace_id=self._resolve_workspace_id(run, item),
                input_artifacts=item.input_artifacts, input_artifact_bindings=item.input_artifact_bindings,
                acceptance_ref=ref,
            ))
        return tasks

    def _resolve_workspace_id(self, run, task):
        ids = list(run.workspaces)
        if task.workspace_id is not None:
            if task.workspace_id not in ids:
                raise OrchestrationError("task references unknown workspace_id")
            return task.workspace_id
        if len(ids) == 1:
            return ids[0]
        if ids:
            raise OrchestrationError("workspace_id required with multiple workspaces")
        return None

    def _resolve_workspaces(self, run_id):
        records = {}
        for key, spec in self.workspace_specs.items():
            if key != spec.workspace_id:
                raise OrchestrationError("workspace key does not match its specification")
            local = spec.source_kind == WorkspaceSourceKind.LOCAL
            root = Path(spec.location).expanduser().resolve() if local else self.run_layout.workspace_repo_dir(run_id, key)
            records[key] = WorkspaceRecord(workspace_id=key, root=str(root), source=spec, managed=not local)
        return records

    @staticmethod
    def _grant(record):
        return WorkspaceGrant(root=record.root, access=record.source.access, source=record.source.source_kind)

    def load(self, run_id):
        return self.store.load(run_id)

    def ready_task_ids(self, run_id):
        return self._ready_task_ids(self.store.load(run_id))

    def _ready_task_ids(self, run):
        if run.workflow is None:
            return []
        statuses = {task.id: task.status for task in run.workflow.tasks}
        return [task.id for task in run.workflow.tasks if task.status == TaskStatus.PENDING
                and all(statuses[dep] == TaskStatus.COMPLETED for dep in task.depends_on)]

    def _resolve_future_artifact_bindings(self, run, task):
        ids = []
        for binding in task.input_artifact_bindings:
            if binding.source_task not in task.depends_on:
                raise OrchestrationError("future binding requires a direct dependency")
            source = self._task(run, binding.source_task)
            if source.status != TaskStatus.COMPLETED or not source.attempts:
                raise OrchestrationError("future artifact source task is not complete")
            latest = source.attempts[-1]
            matches = [key for key in latest.artifact_ids if key in run.artifacts
                       and run.artifacts[key].output_name == binding.output_selector]
            if len(matches) != 1:
                raise OrchestrationError("future artifact selector is missing or ambiguous")
            ids.extend(matches)
        return list(dict.fromkeys(ids))

    def execute_task(self, run_id, task_id):
        check_cancelled()
        run = self.store.load(run_id)
        if run.status in {RunStatus.COMPLETED, RunStatus.PAUSED, RunStatus.FAILED}:
            raise OrchestrationError(f"run is not executable: {run.status}")
        if task_id not in self._ready_task_ids(run):
            raise OrchestrationError("task is not ready")
        task = self._task(run, task_id)
        self._require_remaining_llm_budget(run)
        last = task.attempts[-1] if task.attempts else None
        if last is not None and last.status == AttemptStatus.NEEDS_USER_INPUT:
            if last.session is None:
                raise OrchestrationError("paused attempt has no session")
            attempt = last
            parent = last.session.id
        else:
            number = len(task.attempts) + 1
            if number > run.request.execution_limits.max_attempts_per_task:
                raise OrchestrationError("task attempt limit is exhausted")
            attempt = Attempt(number=number, status=AttemptStatus.RUNNING,
                              started_at=datetime.now(UTC), acceptance_ref=task.acceptance_ref)
            task.attempts.append(attempt)
            parent = None
        task.status = TaskStatus.RUNNING
        attempt.status = AttemptStatus.RUNNING
        run.status = RunStatus.RUNNING
        self._save(run)
        try:
            if parent is None:
                task.input_artifacts = list(dict.fromkeys([
                    *task.input_artifacts, *self._resolve_future_artifact_bindings(run, task),
                ]))
            request = self._module_request(run, task, attempt.number, parent_session_id=parent)
        except Exception as error:
            attempt.status = AttemptStatus.FAILED
            attempt.finished_at = datetime.now(UTC)
            message = str(error).strip() or type(error).__name__
            attempt.error = ModuleError(code=ErrorCode.CONTRACT_ERROR, message=message, retryable=False)
            attempt.report = message
            task.status = TaskStatus.FAILED
            self._persist_attempt_report(run, task, attempt)
            self._evaluate_run(run)
            self._save(run)
            return run
        return self._invoke(run, task, attempt, request)

    def _module_request(self, run, task, attempt_number, *, parent_session_id):
        record = run.workspaces.get(task.workspace_id)
        refs = [run.artifacts[key] for key in task.input_artifacts]
        if task.acceptance_ref:
            refs.append(task.acceptance_ref)
        if run.dataset_catalog_ref:
            refs.append(run.dataset_catalog_ref)
        answers = [answer for answer in run.answers
                   if answer.task_id == task.id and answer.attempt_number == attempt_number]
        fresh_answers = [answer for answer in answers if answer.question_id not in run.delivered_answer_ids]
        controls = [key for key in run.received_controls if parent_session_id and key.startswith(f"session:{parent_session_id}:")]
        origin = None
        if controls:
            _, sid, _, turn, _, call_id = controls[-1].split(":", 5)
            origin = ControlOrigin(session_id=sid, turn=int(turn), call_id=call_id)
        return AgentRequest(
            run_id=run.run_id, task_id=task.id, attempt_number=attempt_number,
            agent=AgentOwner(task.workflow_agent_kind.value), instruction=task.instruction,
            input_artifacts=list({ref.id: ref for ref in refs}.values()),
            budget=TaskBudget(max_llm_calls=run.request.budget.max_llm_calls-run.llm_calls_used,
                              timeout_seconds=max(1, int(run.remaining_timeout_seconds(datetime.now(UTC))))),
            workspace=self._grant(record) if record else None, workspace_id=task.workspace_id,
            workspace_spec=record.source if record else None,
            output_dir=str(self.run_layout.attempt_dir(run.run_id, task.id, attempt_number)),
            permissions=AgentPermissions(**run.request.permissions.model_dump(exclude={"schema_version"})),
            parent_session_id=parent_session_id,
            upstream_session_id=run.scientific_session.id if run.scientific_session else None,
            upstream_inputs=run.task_inputs.get(task.id, []),
            user_answers=[answer for answer in answers if answer.action is None],
            resume_answers=fresh_answers if parent_session_id else [], control_ack=origin,
            task_context={"shared_goal": task.shared_goal, "shared_constraints": task.shared_constraints,
                          "upstream_source": run.scientific_session.id if run.scientific_session else None,
                          "dataset_refs": [ref.model_dump(mode="json") for ref in run.dataset_refs],
                          "acceptance_requirements": read_json(task.acceptance_ref) if task.acceptance_ref else {}},
        )

    def _invoke(self, run, task, attempt, request):
        binding = self.bindings[task.workflow_agent_kind]
        before = set(run.artifacts)
        calls = 0
        try:
            with execution_budget(max_llm_calls=request.budget.max_llm_calls,
                                  timeout_seconds=run.remaining_timeout_seconds(datetime.now(UTC)),
                                  usage=RunUsagePort(run, self.store)):
                attempt.agent_started = True
                self._save(run)
                check_cancelled()
                raw = binding.port.invoke(request)
            value = raw.model_dump() if isinstance(raw, BaseModel) else raw
            claimed = value.get("llm_calls", 0) if isinstance(value, dict) else 0
            if isinstance(claimed, int) and not isinstance(claimed, bool) and claimed >= 0:
                calls = claimed
            result = AgentResult.model_validate(value)
        except OperationCancelled:
            raise
        except Exception as error:
            message = str(error).strip() or type(error).__name__
            result = AgentResult(status=ModuleStatus.FAILED, report=f"Agent invocation rejected: {message}",
                                 llm_calls=calls, error=ModuleError(code=ErrorCode.CONTRACT_ERROR, message=message, retryable=False))
        key = f"task:{task.id}:attempt:{attempt.number}"
        # Tools may have durably registered material through an injected port.
        # Continue from that authoritative Run rather than overwriting it.
        run = self.store.load(run.run_id)
        task = self._task(run, task.id)
        attempt = task.attempts[request.attempt_number - 1]
        run.pending_module_results[key] = PendingModuleResult(
            request=request, result=result, previous_artifact_ids=list(attempt.artifact_ids),
        )
        self._save(run)
        return self._apply_module_result(run, task, attempt, request, result, before=before)

    def _apply_module_result(self, run, task, attempt, request, result, *, before=None):
        before = set(run.artifacts) if before is None else before
        attempt.report = result.report
        attempt.session = result.session
        attempt.warnings = list(result.warnings)
        task.warnings.extend(warning for warning in result.warnings if warning not in task.warnings)
        try:
            if result.session is not None and result.session.module != request.agent:
                raise OrchestrationError("result session owner mismatch")
            if request.parent_session_id and (result.session is None or result.session.id != request.parent_session_id):
                raise OrchestrationError("result does not resume the bound session")
            if result.status == ModuleStatus.REQUEST_WORK:
                raise OrchestrationError("task Agent cannot request work")
            pending = run.pending_module_results[f"task:{task.id}:attempt:{attempt.number}"]
            refs = receive_artifacts(self.artifact_registry, run, request, result,
                                     previous_ids=pending.previous_artifact_ids)
            run.delivered_answer_ids = list(dict.fromkeys([
                *run.delivered_answer_ids, *[answer.question_id for answer in request.resume_answers],
            ]))
            attempt.artifact_ids = list(dict.fromkeys([*attempt.artifact_ids, *[ref.id for ref in refs]]))
            if run.llm_calls_used > run.request.budget.max_llm_calls:
                raise OrchestrationError("run LLM-call budget exhausted")
            if result.status in {ModuleStatus.COMPLETED, ModuleStatus.COMPLETED_WITH_WARNINGS}:
                check_acceptance(run, task, attempt, [run.artifacts[key] for key in attempt.artifact_ids])
            if result.status == ModuleStatus.NEEDS_USER_INPUT:
                receive_control(run, request, result)
        except Exception as error:
            attempt.artifact_ids = list(dict.fromkeys([
                *attempt.artifact_ids, *(key for key in run.artifacts if key not in before),
            ]))
            message = str(error).strip() or type(error).__name__
            failure = (result.error.model_copy(update={
                "retryable": False,
                "details": {**result.error.details, "artifact_registration_error": message},
            }) if result.error is not None else ModuleError(
                code=ErrorCode.ARTIFACT_MISSING if isinstance(error, (ArtifactRegistrationError, OSError)) else ErrorCode.CONTRACT_ERROR,
                message=message, retryable=False,
            ))
            attempt.reception_error = failure
            result = AgentResult(status=ModuleStatus.FAILED, report=attempt.report,
                                 error=failure)
        if result.status in {ModuleStatus.YIELDED, ModuleStatus.INTERRUPTED}:
            attempt.status = AttemptStatus.NEEDS_USER_INPUT
            task.status = TaskStatus.NEEDS_USER_INPUT
            run.parent_attention = result.status == ModuleStatus.YIELDED
            if result.status == ModuleStatus.INTERRUPTED:
                run.status = RunStatus.PAUSED
        elif result.status == ModuleStatus.NEEDS_USER_INPUT:
            attempt.status = AttemptStatus.NEEDS_USER_INPUT
            task.status = TaskStatus.NEEDS_USER_INPUT
        else:
            attempt.finished_at = datetime.now(UTC)
            attempt.status = AttemptStatus(result.status.value)
            attempt.error = result.error
            if result.status in {ModuleStatus.COMPLETED, ModuleStatus.COMPLETED_WITH_WARNINGS}:
                task.status = TaskStatus.COMPLETED
            elif result.status == ModuleStatus.BLOCKED:
                task.status = TaskStatus.BLOCKED
            else:
                retry = result.error and result.error.retryable and attempt.number < run.request.execution_limits.max_attempts_per_task
                task.status = TaskStatus.PENDING if retry else TaskStatus.FAILED
        self._save(run)
        if attempt.status not in {AttemptStatus.RUNNING, AttemptStatus.NEEDS_USER_INPUT}:
            self._persist_attempt_report(run, task, attempt)
        run.pending_module_results.pop(f"task:{task.id}:attempt:{attempt.number}", None)
        if result.error is not None and result.error.details.get("stop_confirmed") is False:
            run.terminal_error = result.error
            run.status = RunStatus.FAILED
        elif result.status != ModuleStatus.INTERRUPTED:
            self._evaluate_run(run)
        self._save(run)
        return run.model_copy(deep=True)

    def _persist_attempt_report(self, run, task, attempt):
        """Preserve original report characters after the final receiving gate has decided."""
        if attempt.report_artifact_id is not None:
            self._acknowledge_attempt_result(run, task, attempt)
            return run.artifacts[attempt.report_artifact_id]
        facts = {
            "task_id": task.id, "attempt_number": attempt.number, "status": attempt.status.value,
            "agent_started": attempt.agent_started,
            "error": attempt.error.model_dump(mode="json") if attempt.error else None,
            "agent_error": (run.pending_module_results[f"task:{task.id}:attempt:{attempt.number}"].result.error.model_dump(mode="json")
                            if f"task:{task.id}:attempt:{attempt.number}" in run.pending_module_results
                            and run.pending_module_results[f"task:{task.id}:attempt:{attempt.number}"].result.error else None),
            "reception_error": attempt.reception_error.model_dump(mode="json") if attempt.reception_error else None,
            "warnings": [warning.model_dump(mode="json") for warning in attempt.warnings],
            "output_artifact_ids": attempt.artifact_ids,
        }
        candidate = ArtifactCandidate(kind="module_report", path="module_report.md", media_type="text/markdown",
                                      summary=(f"Original {task.workflow_agent_kind.value} report for {task.id}, Attempt {attempt.number}; {attempt.status.value}"
                                               if attempt.agent_started else f"Runtime pre-execution failure for {task.id}, Attempt {attempt.number}; Agent not started"),
                                      content=attempt.report, metadata=facts)
        key = f"task:{task.id}:attempt:{attempt.number}:report"
        ref = self.artifact_registry.register_system_artifact(
            candidate, run_id=run.run_id, source_type="task_report", task_id=task.id,
            attempt_number=attempt.number, operation_key=key, run=run,
        )
        attempt.report_artifact_id = ref.id
        self._save(run)
        self._acknowledge_attempt_result(run, task, attempt)
        return ref

    def _acknowledge_attempt_result(self, run, task, attempt):
        # The report binding is the final receiving decision. Persist that the
        # returned result was consumed before dropping its allocation records.
        run.pending_module_results.pop(f"task:{task.id}:attempt:{attempt.number}", None)
        self._save(run)
        prefix = f"task:{task.id}:attempt:{attempt.number}:"
        for pending_key, pending in list(run.pending_registrations.items()):
            if pending_key.startswith(prefix):
                self.artifact_registry.acknowledge_registration(pending_key, run_id=run.run_id, run=run)

    def run_until_stable(self, run_id):
        while True:
            run = self.store.load(run_id)
            if run.status in {RunStatus.COMPLETED, RunStatus.PAUSED, RunStatus.FAILED} or run.parent_attention:
                return run
            if run.llm_calls_used >= run.request.budget.max_llm_calls or run.remaining_timeout_seconds(datetime.now(UTC)) <= 0:
                return run
            check_cancelled()
            ready = self._ready_task_ids(run)
            if not ready:
                self._evaluate_run(run)
                self._save(run)
                return run
            self.execute_task(run_id, ready[0])

    def resume_task_in_place(self, run, task_id):
        task = self._task(run, task_id)
        if task.status != TaskStatus.NEEDS_USER_INPUT:
            raise OrchestrationError("question task is not awaiting user input")
        task.status = TaskStatus.PENDING

    def _recover_interrupted_attempts_in_place(self, run):
        changed = self._acknowledge_acceptance_registrations(run)
        for task in run.workflow.tasks if run.workflow else []:
            for prior in task.attempts:
                pending = run.pending_module_results.get(f"task:{task.id}:attempt:{prior.number}")
                if pending is not None:
                    if prior.report_artifact_id is not None:
                        self._acknowledge_attempt_result(run, task, prior)
                    else:
                        self._apply_module_result(run, task, prior, pending.request, pending.result)
                    changed = True
                    continue
                if prior.status not in {AttemptStatus.RUNNING, AttemptStatus.NEEDS_USER_INPUT} and prior.report_artifact_id is None:
                    self._persist_attempt_report(run, task, prior)
                    changed = True
                elif prior.report_artifact_id is not None and any(
                    key.startswith(f"task:{task.id}:attempt:{prior.number}:") for key in run.pending_registrations
                ):
                    self._acknowledge_attempt_result(run, task, prior)
                    changed = True
            if task.status != TaskStatus.RUNNING:
                continue
            attempt = task.attempts[-1]
            attempt.status = AttemptStatus.FAILED
            attempt.finished_at = datetime.now(UTC)
            attempt.error = ModuleError(code=ErrorCode.INTERRUPTED, message="attempt interrupted before result persistence", retryable=True)
            task.status = TaskStatus.PENDING if attempt.number < run.request.execution_limits.max_attempts_per_task else TaskStatus.FAILED
            self._save(run)
            self._persist_attempt_report(run, task, attempt)
            changed = True
        if changed:
            self._evaluate_run(run)
        return changed

    def apply_patch(self, run_id, patch):
        run = self.store.load(run_id)
        patch = WorkflowPatch.model_validate(patch.model_dump())
        if run.status == RunStatus.COMPLETED or run.workflow is None:
            raise OrchestrationError("workflow cannot be patched")
        if patch.based_on_revision != run.workflow.revision:
            raise OrchestrationError("patch is based on a stale workflow revision")
        validate_workflow_candidate(patch)
        if len(run.workflow.tasks) + len(patch.add_tasks) > run.request.execution_limits.max_tasks:
            raise OrchestrationError("patched workflow exceeds task limit")
        tasks = [*run.workflow.tasks, *self._tasks_from_proposal(run, patch.add_tasks)]
        revised = Workflow(run_id=run_id, revision=run.workflow.revision+1, tasks=tasks, created_from=patch.work_request_id)
        run.workflow_history.append(run.workflow.model_copy(deep=True))
        run.workflow = revised
        self._evaluate_run(run)
        self._save(run)
        self._acknowledge_acceptance_registrations(run)
        return run.model_copy(deep=True)

    def _evaluate_run(self, run):
        if run.pending_question is not None:
            run.status = RunStatus.PAUSED
            return
        if run.workflow is not None:
            tasks = {task.id: task for task in run.workflow.tasks}
            for task_id in TopologicalSorter({key: task.depends_on for key, task in tasks.items()}).static_order():
                task = tasks[task_id]
                if task.status == TaskStatus.PENDING and any(
                    tasks[key].status in {TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.CANCELLED} for key in task.depends_on
                ):
                    task.status = TaskStatus.BLOCKED
            active = self._active_work_request(run)
            active_tasks = [task for task in tasks.values() if active and task.work_request_id == active.id]
            if active_tasks and all(task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.BLOCKED}
                                    for task in active_tasks):
                _transition_work_request(active, WorkRequestStatus.STABLE, workflow_revision=run.workflow.revision,
                                         outcome=self._build_work_outcome(run, active.id))
        run.status = RunStatus.RUNNING

    @staticmethod
    def _active_work_request(run):
        return next((item for item in run.work_requests if item.status == WorkRequestStatus.EXECUTING), None)

    @staticmethod
    def _build_work_outcome(run, work_request_id):
        from .interpreter import DeterministicWorkInterpreter
        work = next(item for item in run.work_requests if item.id == work_request_id)
        # Build from already adjudicated Attempts, never summarize their report bodies.
        previous = work.status
        work.status = WorkRequestStatus.STABLE
        work.workflow_revision = run.workflow.revision
        try:
            return DeterministicWorkInterpreter().interpret(work, tasks=run.workflow.tasks, artifacts=run.artifacts)
        finally:
            work.status = previous

    def _save(self, run):
        run.updated_at = datetime.now(UTC)
        self.store.save(type(run).model_validate(run.model_dump()))

    def _require_bindings(self, kinds):
        for kind in kinds:
            if kind not in self.bindings or self.bindings[kind].owner.value != kind.value:
                raise OrchestrationError(f"no matching Agent binding for {kind}")

    @staticmethod
    def _require_remaining_llm_budget(run):
        if run.llm_calls_used >= run.request.budget.max_llm_calls:
            raise OrchestrationError("run LLM-call budget exhausted")

    @staticmethod
    def _task(run, task_id):
        for task in run.workflow.tasks:
            if task.id == task_id:
                return task
        raise OrchestrationError(f"unknown task: {task_id}")
