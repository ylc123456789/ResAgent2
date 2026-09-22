"""Deterministic scheduling of single-mode Agent invocations."""

from __future__ import annotations

from datetime import UTC, datetime
from graphlib import TopologicalSorter
from pathlib import Path
from uuid import uuid4
from pydantic import BaseModel
from resagent2_runtime.budget import execution_budget
from resagent2_contracts import (
    AgentOwner, AgentPermissions, AgentRequest, AgentResult, Attempt, AttemptStatus, EnvironmentSpec,
    ErrorCode, ModuleError, ModuleStatus, PendingQuestion, QuestionDraft, RunStatus,
    TaskAcceptanceSpec, TaskBudget, TaskStatus, Workflow, WorkflowAgentKind,
    WorkflowPatch, WorkflowProposal, WorkflowTask, WorkOutcome, WorkRequestStatus,
    WorkTaskOutcome, WorkspaceGrant, WorkspaceRecord, WorkspaceSourceKind,
)
from .artifacts import ArtifactRegistrationError, ArtifactRegistry
from .handoffs import check_acceptance, read_json, receive_artifacts, system_artifact
from .layout import RunLayout
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


def _question_id(task_id=None, attempt_number=None):
    return f"question_{uuid4().hex}"


_WORK_REQUEST_TRANSITIONS = {
    WorkRequestStatus.REQUESTED: {WorkRequestStatus.COMPILING, WorkRequestStatus.FAILED},
    WorkRequestStatus.COMPILING: {WorkRequestStatus.EXECUTING, WorkRequestStatus.FAILED},
    WorkRequestStatus.EXECUTING: {WorkRequestStatus.STABLE, WorkRequestStatus.FAILED},
    WorkRequestStatus.STABLE: {WorkRequestStatus.CONSUMED, WorkRequestStatus.FAILED},
    WorkRequestStatus.CONSUMED: set(), WorkRequestStatus.FAILED: set(),
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
        self.artifact_registry = ArtifactRegistry(artifact_root)
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
        return run.model_copy(deep=True)

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
            attempt.error = ModuleError(code=ErrorCode.CONTRACT_ERROR, message=str(error), retryable=False)
            attempt.report = str(error)
            task.status = TaskStatus.FAILED
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
        answers = [ref for ref in run.artifacts.values() if ref.kind == "answer"
                   and ref.task_id == task.id and ref.attempt_number == attempt_number]
        refs.extend(answers)
        fresh_answers = [ref for ref in answers
                         if read_json(ref)["question_id"] not in run.delivered_answer_ids]
        return AgentRequest(
            run_id=run.run_id, task_id=task.id, attempt_number=attempt_number,
            agent=AgentOwner(task.workflow_agent_kind.value), instruction=task.instruction,
            input_artifacts=list({ref.id: ref for ref in refs}.values()),
            budget=TaskBudget(max_llm_calls=run.request.budget.max_llm_calls-run.llm_calls_used,
                              timeout_seconds=max(1, int(run.remaining_timeout_seconds(datetime.now(UTC))))),
            workspace=self._grant(record) if record else None, workspace_id=task.workspace_id,
            workspace_spec=record.source if record else None,
            environment_spec=record.source.environment or EnvironmentSpec() if record else EnvironmentSpec(),
            output_dir=str(self.run_layout.attempt_dir(run.run_id, task.id, attempt_number)),
            permissions=AgentPermissions(**run.request.permissions.model_dump(exclude={"schema_version"})),
            confirm_commands=run.request.confirm_commands,
            parent_session_id=parent_session_id,
            resume_artifact_ids=[ref.id for ref in fresh_answers] if parent_session_id else [],
        )

    def _invoke(self, run, task, attempt, request):
        binding = self.bindings[task.workflow_agent_kind]
        before = set(run.artifacts)
        calls = 0
        try:
            with execution_budget(max_llm_calls=request.budget.max_llm_calls,
                                  timeout_seconds=run.remaining_timeout_seconds(datetime.now(UTC)),
                                  usage=RunUsagePort(run, self.store)):
                raw = binding.port.invoke(request)
            value = raw.model_dump() if isinstance(raw, BaseModel) else raw
            claimed = value.get("llm_calls", 0) if isinstance(value, dict) else 0
            if isinstance(claimed, int) and not isinstance(claimed, bool) and claimed >= 0:
                calls = claimed
            result = AgentResult.model_validate(value)
        except Exception as error:
            result = AgentResult(status=ModuleStatus.FAILED, report=f"Agent invocation rejected: {error}",
                                 llm_calls=calls, error=ModuleError(code=ErrorCode.CONTRACT_ERROR, message=str(error), retryable=False))
        attempt.report = result.report
        attempt.session = result.session
        task.warnings.extend(result.warnings)
        control_ref = None
        try:
            if result.session is not None and result.session.module != binding.owner:
                raise OrchestrationError("result session owner mismatch")
            if request.parent_session_id and (result.session is None or result.session.id != request.parent_session_id):
                raise OrchestrationError("result does not resume the bound session")
            if result.status == ModuleStatus.REQUEST_WORK:
                raise OrchestrationError("task Agent cannot request work")
            refs, control_ref = receive_artifacts(self.artifact_registry, run, request, result, previous_ids=attempt.artifact_ids)
            run.delivered_answer_ids = list(dict.fromkeys([
                *run.delivered_answer_ids,
                *[read_json(ref)["question_id"] for ref in request.input_artifacts
                  if ref.id in request.resume_artifact_ids and ref.kind == "answer"],
            ]))
            attempt.artifact_ids = list(dict.fromkeys([*attempt.artifact_ids, *[ref.id for ref in refs]]))
            if run.llm_calls_used > run.request.budget.max_llm_calls:
                raise OrchestrationError("run LLM-call budget exhausted")
            if result.status in {ModuleStatus.COMPLETED, ModuleStatus.COMPLETED_WITH_WARNINGS}:
                check_acceptance(run, task, attempt, [run.artifacts[key] for key in attempt.artifact_ids])
            if result.status == ModuleStatus.NEEDS_USER_INPUT:
                draft = read_json(control_ref, QuestionDraft)
        except Exception as error:
            attempt.artifact_ids = list(dict.fromkeys([*attempt.artifact_ids, *(set(run.artifacts)-before)]))
            failure = (result.error.model_copy(update={
                "retryable": False,
                "details": {**result.error.details, "artifact_registration_error": str(error)},
            }) if result.error is not None else ModuleError(
                code=ErrorCode.ARTIFACT_MISSING if isinstance(error, (ArtifactRegistrationError, OSError)) else ErrorCode.CONTRACT_ERROR,
                message=str(error), retryable=False,
            ))
            result = AgentResult(status=ModuleStatus.FAILED, report=f"{attempt.report}; reception failed: {error}",
                                 error=failure)
        attempt.report = result.report
        if result.status == ModuleStatus.NEEDS_USER_INPUT:
            attempt.status = AttemptStatus.NEEDS_USER_INPUT
            task.status = TaskStatus.NEEDS_USER_INPUT
            run.pending_question = PendingQuestion(
                id=_question_id(), run_id=run.run_id, task_id=task.id, attempt_number=attempt.number,
                text=draft.text, requested_fields=draft.requested_fields,
                options=draft.options, created_at=datetime.now(UTC), action=draft.action,
            )
            run.pending_question_ref = control_ref
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
        self._evaluate_run(run)
        self._save(run)
        return run.model_copy(deep=True)

    def run_until_stable(self, run_id):
        while True:
            run = self.store.load(run_id)
            if run.status in {RunStatus.COMPLETED, RunStatus.PAUSED, RunStatus.FAILED}:
                return run
            if run.llm_calls_used >= run.request.budget.max_llm_calls or run.remaining_timeout_seconds(datetime.now(UTC)) <= 0:
                return run
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
        changed = False
        for task in run.workflow.tasks if run.workflow else []:
            if task.status != TaskStatus.RUNNING:
                continue
            attempt = task.attempts[-1]
            attempt.status = AttemptStatus.FAILED
            attempt.finished_at = datetime.now(UTC)
            attempt.error = ModuleError(code=ErrorCode.INTERRUPTED, message="attempt interrupted before result persistence", retryable=True)
            task.status = TaskStatus.PENDING if attempt.number < run.request.execution_limits.max_attempts_per_task else TaskStatus.FAILED
            changed = True
        if changed:
            self._evaluate_run(run)
        return changed

    def retry_task(self, run_id, task_id):
        run = self.store.load(run_id)
        task = self._task(run, task_id)
        if task.status not in {TaskStatus.FAILED, TaskStatus.BLOCKED} or len(task.attempts) >= run.request.execution_limits.max_attempts_per_task:
            raise OrchestrationError("task cannot be retried")
        task.status = TaskStatus.PENDING
        run.status = RunStatus.RUNNING
        self._save(run)
        return run.model_copy(deep=True)

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
                    tasks[key].status in {TaskStatus.FAILED, TaskStatus.BLOCKED} for key in task.depends_on
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
        tasks = []
        for task in run.workflow.tasks:
            if task.work_request_id != work_request_id:
                continue
            last = task.attempts[-1] if task.attempts else None
            status = task.status.value
            error = last.error if last else None
            if status != "completed" and error is None:
                error = ModuleError(code=ErrorCode.CONTRACT_ERROR, message="dependency did not complete", retryable=False)
            tasks.append(WorkTaskOutcome(task_id=task.id, status=status, summary=last.report if last and last.report else task.instruction,
                                         artifact_ids=last.artifact_ids if last else [], error=error, warnings=task.warnings))
        return WorkOutcome(work_request_id=work_request_id, workflow_revision=run.workflow.revision, summary="execution stable", tasks=tasks)

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
