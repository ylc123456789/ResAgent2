"""Deterministic Workflow scheduler and state transitions."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from resagent2_contracts import (
    Attempt,
    AttemptStatus,
    Capability,
    ErrorCode,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    ModuleTaskRequest,
    PendingQuestion,
    ResearchRequest,
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

    def create_run(
        self,
        run_id: str,
        request: ResearchRequest,
        proposal: WorkflowProposal,
    ) -> ResearchRun:
        if self.store.exists(run_id):
            raise OrchestrationError(f"run already exists: {run_id}")
        if len(proposal.tasks) > request.budget.max_tasks:
            raise OrchestrationError("workflow exceeds run max_tasks budget")
        self._require_bindings(task.capability for task in proposal.tasks)
        tasks = self._tasks_from_proposal(proposal)
        now = datetime.now(UTC)
        run = ResearchRun(
            run_id=run_id,
            request=request,
            status=RunStatus.RUNNING,
            workflow=Workflow(
                run_id=run_id,
                revision=1,
                tasks=tasks,
                created_from=proposal.work_request_id,
            ),
            workspaces=self._resolve_workspaces(run_id),
            created_at=now,
            updated_at=now,
        )
        self._save(run)
        return run.model_copy(deep=True)

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
                required=item.required,
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
        """Execute exactly one ready task and persist every transition."""

        run = self.store.load(run_id)
        if run.status in {RunStatus.COMPLETED, RunStatus.PAUSED}:
            raise OrchestrationError(f"run is not executable: {run.status.value}")
        if task_id not in self._ready_task_ids(run):
            raise OrchestrationError(f"task is not ready: {task_id}")
        task = self._task(run, task_id)
        binding = self.bindings[task.capability]
        attempt_number = len(task.attempts) + 1
        inherited_artifacts = [
            artifact_id
            for dependency_id in task.depends_on
            for dependency_attempt in self._task(run, dependency_id).attempts
            if dependency_attempt.status
            in {AttemptStatus.COMPLETED, AttemptStatus.COMPLETED_WITH_WARNINGS}
            for artifact_id in dependency_attempt.artifact_ids
        ]
        task.input_artifacts = list(
            dict.fromkeys([*task.input_artifacts, *inherited_artifacts])
        )
        started = datetime.now(UTC)
        task.status = TaskStatus.RUNNING
        task.attempts.append(
            Attempt(
                number=attempt_number,
                status=AttemptStatus.RUNNING,
                started_at=started,
            )
        )
        run.status = RunStatus.RUNNING
        self._save(run)

        previous_attempt = task.attempts[-2] if len(task.attempts) > 1 else None
        previous_session = (
            previous_attempt.session.id
            if previous_attempt is not None
            and previous_attempt.status == AttemptStatus.NEEDS_USER_INPUT
            and previous_attempt.session is not None
            else None
        )
        record = run.workspaces.get(task.workspace_id) if task.workspace_id else None
        grant = self._grant(record, task.capability) if record is not None else None
        output_dir = str(self.run_layout.attempt_dir(run.run_id, task.id, attempt_number))
        inputs = task.inputs
        if task.capability == Capability.EXPERIMENT_RUN and run.request.dataset_refs:
            inputs = inputs.model_copy(
                update={"dataset_refs": list(run.request.dataset_refs)}
            )
        module_request = ModuleTaskRequest(
            run_id=run.run_id,
            task_id=task.id,
            attempt_number=attempt_number,
            capability=task.capability,
            goal=task.goal,
            inputs=inputs,
            input_artifacts=[run.artifacts[item] for item in task.input_artifacts],
            constraints=task.constraints,
            answers=[
                answer
                for answer in run.answers
                if run.answer_task_ids.get(answer.question_id) == task.id
            ],
            budget=TaskBudget(
                max_steps=50,
                max_llm_calls=min(50, run.request.budget.max_llm_calls),
                timeout_seconds=run.request.budget.timeout_seconds,
            ),
            workspace=grant,
            workspace_id=task.workspace_id,
            workspace_spec=record.source if record is not None else None,
            output_dir=output_dir,
            parent_session_id=previous_session,
        )
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
                    attempt_number=attempt_number,
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
        attempt = task.attempts[-1]
        attempt.finished_at = finished
        attempt.session = result.session
        attempt.artifact_ids = artifact_ids
        attempt.payload = result.payload
        attempt.summary = result.summary

        if result.status in {
            ModuleStatus.COMPLETED,
            ModuleStatus.COMPLETED_WITH_WARNINGS,
        }:
            attempt.status = (
                AttemptStatus.COMPLETED_WITH_WARNINGS
                if result.status == ModuleStatus.COMPLETED_WITH_WARNINGS
                else AttemptStatus.COMPLETED
            )
            task.status = TaskStatus.COMPLETED
            task.warnings.extend(result.warnings)
        elif result.status == ModuleStatus.FAILED:
            attempt.status = AttemptStatus.FAILED
            attempt.error = result.error
            can_retry = (
                result.error is not None
                and result.error.retryable
                and attempt_number < run.request.budget.max_attempts_per_task
            )
            task.status = TaskStatus.PENDING if can_retry else TaskStatus.FAILED
        elif result.status == ModuleStatus.BLOCKED:
            attempt.status = AttemptStatus.BLOCKED
            attempt.error = result.error
            task.status = TaskStatus.BLOCKED
        else:
            attempt.status = AttemptStatus.NEEDS_USER_INPUT
            task.status = TaskStatus.NEEDS_USER_INPUT
            question_id = f"question_{task.id.removeprefix('task_')}_{attempt_number}"
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

        self._evaluate_run(run)
        self._save(run)
        return run.model_copy(deep=True)

    def run_until_stable(self, run_id: str) -> ResearchRun:
        """Execute ready tasks in stable order until complete, paused, or stuck."""

        while True:
            run = self.store.load(run_id)
            if run.status in {RunStatus.COMPLETED, RunStatus.PAUSED}:
                return run
            ready = self._ready_task_ids(run)
            if not ready:
                self._evaluate_run(run)
                self._save(run)
                return run
            self.execute_task(run_id, ready[0])

    def answer_question(self, run_id: str, answer: UserAnswer) -> ResearchRun:
        """Validate one answer, clear the pause and make its task pending again."""

        run = self.store.load(run_id)
        question = run.pending_question
        if question is None or answer.question_id != question.id:
            raise OrchestrationError("answer does not match pending question")
        missing = set(question.requested_fields) - set(answer.values)
        if missing:
            raise OrchestrationError(f"answer is missing fields: {sorted(missing)}")
        run.answers.append(answer)
        run.answer_task_ids[answer.question_id] = question.task_id
        if question.task_id is not None:
            task = self._task(run, question.task_id)
            if task.status != TaskStatus.NEEDS_USER_INPUT:
                raise OrchestrationError("question task is not awaiting user input")
            task.status = TaskStatus.PENDING
        run.pending_question = None
        run.status = RunStatus.RUNNING
        self._save(run)
        return run.model_copy(deep=True)

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
        by_id = {task.id: task for task in tasks}
        for task_id in patch.supersede_task_ids:
            target = by_id.get(task_id)
            if target is None or target.status != TaskStatus.PENDING:
                raise OrchestrationError("only existing pending tasks can be superseded")
            if target.work_request_id != patch.work_request_id:
                raise OrchestrationError(
                    "patch cannot supersede a task from another work request"
                )
            target.status = TaskStatus.SUPERSEDED
        for update in patch.pending_task_updates:
            target = by_id.get(update.task_id)
            if target is None or target.status != TaskStatus.PENDING:
                raise OrchestrationError("only existing pending tasks can be updated")
            if target.work_request_id != patch.work_request_id:
                raise OrchestrationError(
                    "patch cannot update a task from another work request"
                )
            if update.inputs is not None:
                target.inputs = update.inputs
            if update.depends_on is not None:
                target.depends_on = update.depends_on
        for item in patch.add_tasks:
            tasks.append(
                WorkflowTask(
                    id=item.id,
                    work_request_id=item.work_request_id,
                    capability=item.capability,
                    goal=item.goal,
                    inputs=item.inputs,
                    depends_on=item.depends_on,
                    required=item.required,
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

        # Execution graph is stable. With an active work request, freeze a
        # WorkOutcome and mark it stable so the controller can resume the
        # Scientific Session. A work request already stable is left for the
        # controller. Without any work request (a scheduler-driven run that
        # never went through the ResearchController), fall back to required-task
        # completion semantics.
        active = self._active_work_request(run)
        if active is not None and active.status == WorkRequestStatus.EXECUTING:
            active.workflow_revision = run.workflow.revision
            active.outcome = self._build_work_outcome(run, active.id)
            active.status = WorkRequestStatus.STABLE
            run.status = RunStatus.RUNNING
            return
        if active is not None and active.status == WorkRequestStatus.STABLE:
            run.status = RunStatus.RUNNING
            return

        required = [
            task
            for task in run.workflow.tasks
            if task.required and task.status != TaskStatus.SUPERSEDED
        ]
        if all(task.status == TaskStatus.COMPLETED for task in required):
            run.status = RunStatus.COMPLETED
        else:
            run.status = RunStatus.FAILED

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
            TaskStatus.SUPERSEDED: "superseded",
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
    def _task(run: ResearchRun, task_id: str) -> WorkflowTask:
        for task in run.workflow.tasks:
            if task.id == task_id:
                return task
        raise OrchestrationError(f"unknown task: {task_id}")
