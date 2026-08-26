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
    TaskStatus,
    UserAnswer,
    Workflow,
    WorkflowPatch,
    WorkflowProposal,
    WorkflowTask,
)

from .artifacts import ArtifactRegistrationError, ArtifactRegistry
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
    ) -> None:
        self.bindings = dict(bindings)
        self.store = store or InMemoryRunStore()
        self.artifact_registry = ArtifactRegistry(artifact_root)

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
        tasks = [
            WorkflowTask(
                id=item.id,
                capability=item.capability,
                goal=item.goal,
                inputs=item.inputs,
                depends_on=item.depends_on,
                required=item.required,
                success_criteria=item.success_criteria,
            )
            for item in proposal.tasks
        ]
        now = datetime.now(UTC)
        run = ResearchRun(
            run_id=run_id,
            request=request,
            status=RunStatus.RUNNING,
            workflow=Workflow(
                run_id=run_id,
                revision=1,
                tasks=tasks,
                created_from=proposal.summary,
            ),
            created_at=now,
            updated_at=now,
        )
        self._save(run)
        return run.model_copy(deep=True)

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
        module_request = ModuleTaskRequest(
            run_id=run.run_id,
            task_id=task.id,
            attempt_number=attempt_number,
            capability=task.capability,
            goal=task.goal,
            inputs=task.inputs,
            input_artifacts=[run.artifacts[item] for item in task.input_artifacts],
            constraints=run.request.constraints,
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
            workspace=binding.workspace,
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
                    grant=binding.workspace,
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
            target.status = TaskStatus.SUPERSEDED
        for update in patch.pending_task_updates:
            target = by_id.get(update.task_id)
            if target is None or target.status != TaskStatus.PENDING:
                raise OrchestrationError("only existing pending tasks can be updated")
            if update.inputs is not None:
                target.inputs = update.inputs
            if update.depends_on is not None:
                target.depends_on = update.depends_on
        for item in patch.add_tasks:
            tasks.append(
                WorkflowTask(
                    id=item.id,
                    capability=item.capability,
                    goal=item.goal,
                    inputs=item.inputs,
                    depends_on=item.depends_on,
                    required=item.required,
                    success_criteria=item.success_criteria,
                )
            )
        try:
            revised = Workflow(
                run_id=run.run_id,
                revision=run.workflow.revision + 1,
                tasks=tasks,
                created_from=patch.reason,
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
        if self._ready_task_ids(run) or any(
            task.status == TaskStatus.RUNNING for task in run.workflow.tasks
        ):
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
