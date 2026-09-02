"""Deterministic Workflow scheduler and state transitions."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from resagent2_contracts import (
    AgentOwner,
    Attempt,
    AttemptStatus,
    Capability,
    EnvironmentSpec,
    ErrorCode,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    ModuleTaskRequest,
    PendingQuestion,
    RunStatus,
    TaskBudget,
    TaskProposal,
    TaskStatus,
    UserAnswer,
    Workflow,
    WorkflowPatch,
    WorkflowProposal,
    WorkflowTask,
    WorkOutcome,
    WorkRequestStatus,
    WorkTaskOutcome,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceRecord,
    WorkspaceSourceKind,
    WorkspaceSpec,
)

from .artifacts import ArtifactRegistrationError, ArtifactRegistry
from .layout import RunLayout
from .models import ResearchRun
from .ports import ModuleBinding
from .store import InMemoryRunStore, RunStore


class OrchestrationError(ValueError):
    """Raised when a requested orchestration transition is invalid."""


def _validate_answer(question: PendingQuestion | None, answer: UserAnswer) -> None:
    """Validate one answer against the pending question (shared by both layers)."""
    if question is None or answer.question_id != question.id:
        raise OrchestrationError("answer does not match pending question")
    missing = set(question.requested_fields) - set(answer.values)
    if missing:
        raise OrchestrationError(f"answer is missing fields: {sorted(missing)}")


def _question_id(task_id: str, attempt_number: int) -> str:
    """Build a strictly bounded question id from a task id and attempt.

    ``QuestionId`` allows a 128-character body, but a legal task id body is
    itself up to 128 characters and ``max_attempts_per_task`` has no upper
    bound, so ``question_<task>_<attempt>`` can overflow. Prefer the readable
    form; when it would overflow, fall back to a fixed-size hash of the full
    (task, attempt) pair, which is deterministic and unique within a run.
    """
    candidate = f"question_{task_id.removeprefix('task_')}_{attempt_number}"
    if len(candidate.removeprefix("question_")) <= 128:
        return candidate
    digest = hashlib.sha256(
        f"{task_id}:{attempt_number}".encode("utf-8")
    ).hexdigest()[:24]
    return f"question_{digest}"


_WORK_REQUEST_TRANSITIONS: dict[WorkRequestStatus, frozenset[WorkRequestStatus]] = {
    WorkRequestStatus.REQUESTED: frozenset(
        {WorkRequestStatus.COMPILING, WorkRequestStatus.FAILED}
    ),
    WorkRequestStatus.COMPILING: frozenset(
        {WorkRequestStatus.EXECUTING, WorkRequestStatus.FAILED}
    ),
    WorkRequestStatus.EXECUTING: frozenset(
        {WorkRequestStatus.STABLE, WorkRequestStatus.FAILED}
    ),
    WorkRequestStatus.STABLE: frozenset(
        {WorkRequestStatus.CONSUMED, WorkRequestStatus.FAILED}
    ),
    WorkRequestStatus.CONSUMED: frozenset(),
    WorkRequestStatus.FAILED: frozenset(),
}


def _transition_work_request(
    work_request: WorkRequest,
    status: WorkRequestStatus,
    *,
    workflow_revision: int | None = None,
    outcome: WorkOutcome | None = None,
    error: ModuleError | None = None,
) -> None:
    """Centralize the legal WorkRequest transitions and the ``updated_at`` stamp.

    The only legal path is ``requested → compiling → executing → stable →
    consumed``, with ``failed`` reachable from any non-terminal state. Each
    transition also stamps ``updated_at`` (ADR-0011 §1).
    """
    allowed = _WORK_REQUEST_TRANSITIONS.get(work_request.status, frozenset())
    if status not in allowed:
        raise OrchestrationError(
            f"illegal work request transition: "
            f"{work_request.status.value} -> {status.value}"
        )
    work_request.status = status
    work_request.updated_at = datetime.now(UTC)
    if workflow_revision is not None:
        work_request.workflow_revision = workflow_revision
    if outcome is not None:
        work_request.outcome = outcome
    if error is not None:
        work_request.error = error


class WorkflowScheduler:
    """Deterministically execute accepted WorkflowTasks through ModulePorts."""

    def __init__(
        self,
        *,
        bindings: dict[Capability, ModuleBinding],
        store: RunStore | None = None,
        artifact_root: str | Path = ".resagent2/artifacts",
        data_root: str | Path | None = None,
        workspaces: dict[str, WorkspaceSpec] | None = None,
    ) -> None:
        self.bindings = dict(bindings)
        self.store = store or InMemoryRunStore()
        self.artifact_registry = ArtifactRegistry(artifact_root)
        self.run_layout = RunLayout(data_root) if data_root else RunLayout.from_env()
        self.workspace_specs = dict(workspaces or {})

    def accept_proposal(
        self,
        run_id: str,
        proposal: WorkflowProposal,
    ) -> ResearchRun:
        """Attach a compiled proposal as the initial workflow of an existing run."""
        run = self.store.load(run_id)
        if run.workflow is not None:
            raise OrchestrationError("run already has an accepted workflow")
        if len(proposal.tasks) > run.request.budget.max_tasks:
            raise OrchestrationError("workflow exceeds run max_tasks budget")
        self._require_bindings(task.capability for task in proposal.tasks)
        run.workspaces = self._resolve_workspaces(run_id)
        run.workflow = Workflow(
            run_id=run_id,
            revision=1,
            tasks=self._tasks_from_proposal(proposal),
            created_from=proposal.work_request_id,
        )
        self._save(run)
        return run.model_copy(deep=True)

    def _tasks_from_proposal(self, proposal: WorkflowProposal) -> list[WorkflowTask]:
        return [
            WorkflowTask(
                id=item.id,
                work_request_id=item.work_request_id,
                capability=item.capability,
                goal=item.goal,
                inputs=item.inputs,
                depends_on=item.depends_on,
                workspace_id=self._resolve_workspace_id(item),
                constraints=list(item.constraints),
            )
            for item in proposal.tasks
        ]

    def _resolve_workspace_id(self, task: TaskProposal) -> str | None:
        """Fill or validate one task's workspace_id against declared workspaces.

        A single declared workspace is filled in automatically; a compiler that
        invents an undeclared id is rejected.
        """
        ids = list(self.workspace_specs.keys())
        if task.workspace_id is not None:
            if task.workspace_id not in ids:
                raise OrchestrationError(
                    f"task {task.id} references unknown workspace_id "
                    f"{task.workspace_id!r}"
                )
            return task.workspace_id
        if len(ids) == 1:
            return ids[0]
        if ids:
            raise OrchestrationError(
                f"task {task.id} must declare a workspace_id (multiple workspaces exist)"
            )
        return None

    def _resolve_workspaces(self, run_id: str) -> dict[str, WorkspaceRecord]:
        """Resolve declared workspace specs into physical records for one run."""
        records: dict[str, WorkspaceRecord] = {}
        for workspace_id, spec in self.workspace_specs.items():
            if spec.workspace_id != workspace_id:
                raise OrchestrationError(
                    f"workspace spec id {spec.workspace_id!r} does not match key "
                    f"{workspace_id!r}"
                )
            if spec.source_kind == WorkspaceSourceKind.LOCAL:
                if spec.location is None:
                    raise OrchestrationError(
                        f"LOCAL workspace {workspace_id!r} requires a location"
                    )
                root = str(Path(spec.location).expanduser().resolve())
                managed = False
            else:
                root = str(self.run_layout.workspace_repo_dir(run_id, workspace_id))
                managed = True
            records[workspace_id] = WorkspaceRecord(
                workspace_id=workspace_id,
                root=root,
                source=spec,
                managed=managed,
            )
        return records

    @staticmethod
    def _grant(record: WorkspaceRecord, capability: Capability) -> WorkspaceGrant:
        """Derive the per-attempt boundary from a resolved workspace record."""
        writable = capability in {Capability.CODE_MODIFY, Capability.EXPERIMENT_RUN}
        return WorkspaceGrant(
            root=record.root,
            mode=WorkspaceMode.READ_WRITE if writable else WorkspaceMode.READ_ONLY,
            source=record.source.source_kind,
        )

    def load(self, run_id: str) -> ResearchRun:
        """Load the current validated run state."""

        return self.store.load(run_id)

    def ready_task_ids(self, run_id: str) -> list[str]:
        """Return pending tasks whose dependencies are all completed, in graph order."""

        return self._ready_task_ids(self.store.load(run_id))

    def _ready_task_ids(self, run: ResearchRun) -> list[str]:
        status = {task.id: task.status for task in run.workflow.tasks}
        return [
            task.id
            for task in run.workflow.tasks
            if task.status == TaskStatus.PENDING
            and all(status[dependency] == TaskStatus.COMPLETED for dependency in task.depends_on)
        ]

    def execute_task(self, run_id: str, task_id: str) -> ResearchRun:
        """Execute or resume one ready task and persist every transition.

        A task whose last Attempt is paused for user input is resumed on the
        same Attempt (number, Session, output_dir, baseline); any other ready
        task starts a fresh Attempt (ADR-0011 §2).
        """

        run = self.store.load(run_id)
        if run.status in {RunStatus.COMPLETED, RunStatus.PAUSED}:
            raise OrchestrationError(f"run is not executable: {run.status.value}")
        if task_id not in self._ready_task_ids(run):
            raise OrchestrationError(f"task is not ready: {task_id}")
        task = self._task(run, task_id)
        last = task.attempts[-1] if task.attempts else None
        if last is not None and last.status == AttemptStatus.NEEDS_USER_INPUT:
            return self._resume_task(run, task, last)
        return self._start_task(run, task)

    def _start_task(self, run: ResearchRun, task: WorkflowTask) -> ResearchRun:
        """Start a new Attempt for a fresh or retried task."""
        attempt_number = len(task.attempts) + 1
        if attempt_number > run.request.budget.max_attempts_per_task:
            raise OrchestrationError("task attempt budget is exhausted")
        self._require_remaining_llm_budget(run)
        import_artifacts = [
            artifact_id
            for artifact_id, artifact in run.artifacts.items()
            if artifact.producer == AgentOwner.ORCHESTRATOR
            and artifact.metadata.get("source_type") == "import"
        ]
        inherited_artifacts = [
            artifact_id
            for dependency_id in task.depends_on
            for dependency_attempt in self._task(run, dependency_id).attempts
            if dependency_attempt.status
            in {AttemptStatus.COMPLETED, AttemptStatus.COMPLETED_WITH_WARNINGS}
            for artifact_id in dependency_attempt.artifact_ids
        ]
        # Imported input artifacts are authorized to every task; dependency
        # artifacts are added on top (ADR-0011 §4).
        task.input_artifacts = list(
            dict.fromkeys(
                [*task.input_artifacts, *import_artifacts, *inherited_artifacts]
            )
        )
        started = datetime.now(UTC)
        task.status = TaskStatus.RUNNING
        attempt = Attempt(
            number=attempt_number,
            status=AttemptStatus.RUNNING,
            started_at=started,
        )
        task.attempts.append(attempt)
        run.status = RunStatus.RUNNING
        self._save(run)
        module_request = self._module_request(
            run, task, attempt_number, parent_session_id=None
        )
        return self._invoke(run, task, attempt, module_request)

    def _resume_task(
        self, run: ResearchRun, task: WorkflowTask, attempt: Attempt
    ) -> ResearchRun:
        """Resume a paused Attempt on the same number, Session and output_dir."""
        if attempt.session is None:
            raise OrchestrationError("paused attempt has no session to resume")
        attempt.status = AttemptStatus.RUNNING
        task.status = TaskStatus.RUNNING
        run.status = RunStatus.RUNNING
        self._save(run)
        module_request = self._module_request(
            run, task, attempt.number, parent_session_id=attempt.session.id
        )
        return self._invoke(run, task, attempt, module_request)

    def _module_request(
        self,
        run: ResearchRun,
        task: WorkflowTask,
        attempt_number: int,
        *,
        parent_session_id: str | None,
    ) -> ModuleTaskRequest:
        record = run.workspaces.get(task.workspace_id) if task.workspace_id else None
        grant = self._grant(record, task.capability) if record is not None else None
        output_dir = str(self.run_layout.attempt_dir(run.run_id, task.id, attempt_number))
        environment_spec = (
            record.source.environment
            if record is not None and record.source.environment is not None
            else EnvironmentSpec()
        )
        remaining_calls = run.request.budget.max_llm_calls - run.llm_calls_used
        if remaining_calls <= 0:
            raise OrchestrationError("run LLM-call budget is exhausted")
        return ModuleTaskRequest(
            run_id=run.run_id,
            task_id=task.id,
            attempt_number=attempt_number,
            capability=task.capability,
            goal=task.goal,
            inputs=task.inputs,
            input_artifacts=[run.artifacts[item] for item in task.input_artifacts],
            dataset_refs=list(run.request.dataset_refs),
            constraints=task.constraints,
            answers=[
                answer
                for answer in run.answers
                if run.answer_task_ids.get(answer.question_id) == task.id
            ],
            budget=TaskBudget(
                max_steps=50,
                max_llm_calls=min(50, remaining_calls),
                timeout_seconds=max(
                    1,
                    int(
                        run.request.budget.timeout_seconds
                        - (datetime.now(UTC) - run.created_at).total_seconds()
                    ),
                ),
            ),
            workspace=grant,
            workspace_id=task.workspace_id,
            workspace_spec=record.source if record is not None else None,
            environment_spec=environment_spec,
            output_dir=output_dir,
            parent_session_id=parent_session_id,
        )

    def _invoke(
        self,
        run: ResearchRun,
        task: WorkflowTask,
        attempt: Attempt,
        module_request: ModuleTaskRequest,
    ) -> ResearchRun:
        """Invoke the bound port, register artifacts and finalize the Attempt."""
        binding = self.bindings[task.capability]
        record = run.workspaces.get(task.workspace_id) if task.workspace_id else None
        grant = self._grant(record, task.capability) if record is not None else None
        try:
            result = ModuleResult.model_validate(binding.port.invoke(module_request))
        except ValidationError as error:
            result = ModuleResult(
                status=ModuleStatus.FAILED,
                summary="ModulePort returned an invalid contract",
                error=ModuleError(
                    code=ErrorCode.CONTRACT_ERROR,
                    message="ModulePort result failed schema validation",
                    retryable=False,
                    details={
                        "validation_errors": [
                            {
                                "type": item["type"],
                                "loc": list(item["loc"]),
                                "message": item["msg"],
                            }
                            for item in error.errors(include_url=False)
                        ]
                    },
                ),
            )
        except Exception as error:
            result = ModuleResult(
                status=ModuleStatus.FAILED,
                summary="ModulePort invocation failed",
                error=ModuleError(
                    code=ErrorCode.TOOL_FAILED,
                    message=str(error) or type(error).__name__,
                    retryable=True,
                    details={"component": "module_port"},
                ),
            )

        artifact_ids: list[str] = []
        try:
            for index, candidate in enumerate(result.artifacts, start=1):
                artifact = self.artifact_registry.register(
                    candidate,
                    grant=grant,
                    producer=binding.owner,
                    run_id=run.run_id,
                    task_id=task.id,
                    attempt_number=attempt.number,
                    index=index,
                    existing_ids=set(run.artifacts),
                )
                run.artifacts[artifact.id] = artifact
                artifact_ids.append(artifact.id)
        except (ArtifactRegistrationError, OSError) as error:
            result = ModuleResult(
                status=ModuleStatus.FAILED,
                summary="Artifact registration failed",
                error=ModuleError(
                    code=ErrorCode.ARTIFACT_MISSING,
                    message=str(error),
                    retryable=False,
                ),
            )

        finished = datetime.now(UTC)
        # Accumulate this attempt's real LLM calls into the run ledger (ADR-0011 §7).
        run.llm_calls_used += result.llm_calls
        attempt.session = result.session
        attempt.artifact_ids = artifact_ids
        attempt.payload = result.payload
        attempt.summary = result.summary

        if result.status in {
            ModuleStatus.COMPLETED,
            ModuleStatus.COMPLETED_WITH_WARNINGS,
        }:
            attempt.finished_at = finished
            attempt.status = (
                AttemptStatus.COMPLETED_WITH_WARNINGS
                if result.status == ModuleStatus.COMPLETED_WITH_WARNINGS
                else AttemptStatus.COMPLETED
            )
            task.status = TaskStatus.COMPLETED
            task.warnings.extend(result.warnings)
        elif result.status == ModuleStatus.FAILED:
            attempt.finished_at = finished
            attempt.status = AttemptStatus.FAILED
            attempt.error = result.error
            can_retry = (
                result.error is not None
                and result.error.retryable
                and attempt.number < run.request.budget.max_attempts_per_task
            )
            task.status = TaskStatus.PENDING if can_retry else TaskStatus.FAILED
        elif result.status == ModuleStatus.BLOCKED:
            attempt.finished_at = finished
            attempt.status = AttemptStatus.BLOCKED
            attempt.error = result.error
            task.status = TaskStatus.BLOCKED
        elif result.status == ModuleStatus.NEEDS_USER_INPUT:
            attempt.finished_at = None
            attempt.status = AttemptStatus.NEEDS_USER_INPUT
            task.status = TaskStatus.NEEDS_USER_INPUT
            question_id = _question_id(task.id, attempt.number)
            draft = result.question
            if draft is None:
                raise OrchestrationError("needs_user_input result has no question")
            run.pending_question = PendingQuestion(
                id=question_id,
                run_id=run.run_id,
                task_id=task.id,
                text=draft.text,
                requested_fields=draft.requested_fields,
                created_at=finished,
            )
        else:
            # request_work belongs only to the ScientificPort boundary. A task
            # module returning it is an invalid contract, never an implicit
            # request for user input.
            attempt.finished_at = finished
            attempt.status = AttemptStatus.FAILED
            attempt.error = ModuleError(
                code=ErrorCode.CONTRACT_ERROR,
                message="task module returned request_work",
                retryable=False,
            )
            task.status = TaskStatus.FAILED

        self._evaluate_run(run)
        self._save(run)
        return run.model_copy(deep=True)

    def run_until_stable(self, run_id: str) -> ResearchRun:
        """Execute ready tasks in stable order until complete, paused, or stuck."""

        while True:
            run = self.store.load(run_id)
            if run.status in {RunStatus.COMPLETED, RunStatus.PAUSED}:
                return run
            # Stop executing tasks once the run budget is spent; the controller
            # decides the run has failed (ADR-0011 §7).
            if run.llm_calls_used >= run.request.budget.max_llm_calls:
                return run
            if (datetime.now(UTC) - run.created_at).total_seconds() >= run.request.budget.timeout_seconds:
                return run
            ready = self._ready_task_ids(run)
            if not ready:
                self._evaluate_run(run)
                self._save(run)
                return run
            self.execute_task(run_id, ready[0])

    def resume_task_in_place(self, run: ResearchRun, task_id: str) -> None:
        """Resume a paused task by mutating ``run`` in place (no reload/save).

        Called by ``ResearchController.answer_question`` so the answer and the
        task transition are persisted atomically in one ResearchRun snapshot;
        a crash between the two can never leave a question cleared while its
        task stays paused (ADR-0011 §1).
        """

        task = self._task(run, task_id)
        if task.status != TaskStatus.NEEDS_USER_INPUT:
            raise OrchestrationError("question task is not awaiting user input")
        task.status = TaskStatus.PENDING

    def _recover_interrupted_attempts_in_place(self, run: ResearchRun) -> bool:
        """Apply interrupted-Attempt recovery to one Controller-loaded Run.

        ``RUNNING`` records intent to invoke a module; it does not prove that a
        process still exists after restart. Recovery preserves that historical
        Attempt as a retryable interrupted failure, then returns the Task to
        PENDING only when its ordinary retry budget permits another Attempt.
        This stays private because only ResearchController owns Run recovery.
        """
        if run.workflow is None:
            return False
        changed = False
        now = datetime.now(UTC)
        for task in run.workflow.tasks:
            if task.status != TaskStatus.RUNNING:
                continue
            attempt = task.attempts[-1] if task.attempts else None
            if attempt is None or attempt.status != AttemptStatus.RUNNING:
                raise OrchestrationError(
                    f"running task {task.id} has no running latest attempt"
                )
            attempt.status = AttemptStatus.FAILED
            attempt.finished_at = now
            attempt.error = ModuleError(
                code=ErrorCode.INTERRUPTED,
                message="attempt interrupted before its module result was persisted",
                retryable=True,
            )
            task.status = (
                TaskStatus.PENDING
                if attempt.number < run.request.budget.max_attempts_per_task
                else TaskStatus.FAILED
            )
            changed = True
        if changed:
            self._evaluate_run(run)
        return changed

    def retry_task(self, run_id: str, task_id: str) -> ResearchRun:
        """Explicitly retry a failed or blocked task after external recovery."""

        run = self.store.load(run_id)
        task = self._task(run, task_id)
        if task.status not in {TaskStatus.FAILED, TaskStatus.BLOCKED}:
            raise OrchestrationError("only failed or blocked tasks can be retried")
        if len(task.attempts) >= run.request.budget.max_attempts_per_task:
            raise OrchestrationError("task attempt budget is exhausted")
        task.status = TaskStatus.PENDING
        run.status = RunStatus.RUNNING
        self._save(run)
        return run.model_copy(deep=True)

    def apply_patch(self, run_id: str, patch: WorkflowPatch) -> ResearchRun:
        """Apply a revision-bound patch without changing executed task history."""

        run = self.store.load(run_id)
        if run.status == RunStatus.COMPLETED:
            raise OrchestrationError("completed workflow cannot be patched")
        if patch.based_on_revision != run.workflow.revision:
            raise OrchestrationError("patch is based on a stale workflow revision")
        if len(run.workflow.tasks) + len(patch.add_tasks) > run.request.budget.max_tasks:
            raise OrchestrationError("patched workflow exceeds max_tasks budget")
        self._require_bindings(task.capability for task in patch.add_tasks)
        tasks = [task.model_copy(deep=True) for task in run.workflow.tasks]
        for item in patch.add_tasks:
            tasks.append(
                WorkflowTask(
                    id=item.id,
                    work_request_id=item.work_request_id,
                    capability=item.capability,
                    goal=item.goal,
                    inputs=item.inputs,
                    depends_on=item.depends_on,
                    workspace_id=self._resolve_workspace_id(item),
                    constraints=list(item.constraints),
                )
            )
        try:
            revised = Workflow(
                run_id=run.run_id,
                revision=run.workflow.revision + 1,
                tasks=tasks,
                created_from=patch.work_request_id,
            )
        except ValidationError as error:
            raise OrchestrationError(f"invalid patched workflow: {error}") from error
        run.workflow_history.append(run.workflow.model_copy(deep=True))
        run.workflow = revised
        self._evaluate_run(run)
        self._save(run)
        return run.model_copy(deep=True)

    def _evaluate_run(self, run: ResearchRun) -> None:
        if run.pending_question is not None:
            run.status = RunStatus.PAUSED
            return
        if run.workflow is None:
            run.status = RunStatus.RUNNING
            return
        if self._ready_task_ids(run) or any(
            task.status == TaskStatus.RUNNING for task in run.workflow.tasks
        ):
            run.status = RunStatus.RUNNING
            return

        # The execution graph is stable. The scheduler never decides that a
        # ResearchRun has completed: that is the controller's job (ADR-0011 §1).
        # With an active executing work request, freeze a WorkOutcome and mark it
        # stable so the controller can resume the Scientific Session. Otherwise
        # there is nothing left for the scheduler to do, and the run stays
        # running until the controller decides the next step.
        active = self._active_work_request(run)
        if active is not None and active.status == WorkRequestStatus.EXECUTING:
            _transition_work_request(
                active,
                WorkRequestStatus.STABLE,
                workflow_revision=run.workflow.revision,
                outcome=self._build_work_outcome(run, active.id),
            )
        run.status = RunStatus.RUNNING

    @staticmethod
    def _active_work_request(run: ResearchRun):
        for work_request in run.work_requests:
            if work_request.status in {
                WorkRequestStatus.EXECUTING,
                WorkRequestStatus.STABLE,
            }:
                return work_request
        return None

    @staticmethod
    def _build_work_outcome(run: ResearchRun, work_request_id: str) -> WorkOutcome:
        status_by_task = {
            TaskStatus.COMPLETED: "completed",
            TaskStatus.FAILED: "failed",
            TaskStatus.BLOCKED: "blocked",
        }
        tasks: list[WorkTaskOutcome] = []
        for task in run.workflow.tasks:
            if task.work_request_id != work_request_id:
                continue
            outcome_status = status_by_task.get(task.status)
            if outcome_status is None:
                continue
            last = task.attempts[-1] if task.attempts else None
            tasks.append(
                WorkTaskOutcome(
                    task_id=task.id,
                    status=outcome_status,
                    summary=(last.summary if last and last.summary else task.goal),
                    artifact_ids=list(last.artifact_ids) if last else [],
                    error=last.error if last else None,
                    warnings=list(task.warnings),
                )
            )
        return WorkOutcome(
            work_request_id=work_request_id,
            workflow_revision=run.workflow.revision,
            summary="execution stable",
            tasks=tasks,
        )

    def _save(self, run: ResearchRun) -> None:
        run.updated_at = datetime.now(UTC)
        self.store.save(ResearchRun.model_validate(run.model_dump()))

    def _require_bindings(self, capabilities) -> None:
        missing = [item.value for item in capabilities if item not in self.bindings]
        if missing:
            raise OrchestrationError(f"no ModulePort binding for: {sorted(set(missing))}")

    @staticmethod
    def _require_remaining_llm_budget(run: ResearchRun) -> None:
        if run.llm_calls_used >= run.request.budget.max_llm_calls:
            raise OrchestrationError("run LLM-call budget is exhausted")

    @staticmethod
    def _task(run: ResearchRun, task_id: str) -> WorkflowTask:
        for task in run.workflow.tasks:
            if task.id == task_id:
                return task
        raise OrchestrationError(f"unknown task: {task_id}")
