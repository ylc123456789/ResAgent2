"""Route Scientific's explicit tasks without rewriting their requirements."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from resagent2_runtime.budget import (
    BudgetExhaustedError, DeadlineExceededError, current_budget, invoke_model,
)

from resagent2_contracts import (
    FutureArtifactBinding, ExecutionLimits, TaskProposal, Workflow, WorkflowAgentKind,
    WorkflowAgentRegistry, WorkflowPatch, WorkflowProposal, WorkRequest, WorkspaceDescriptor,
)
from resagent2_runtime.cancellation import OperationCancelled
from .workflow_validation import validate_workflow_candidate


@dataclass(frozen=True)
class CompilationResult:
    output: WorkflowProposal | WorkflowPatch
    llm_calls: int = 0


class CompilationError(ValueError):
    def __init__(self, message: str, *, llm_calls: int = 0) -> None:
        super().__init__(message)
        if isinstance(llm_calls, bool) or not isinstance(llm_calls, int) or llm_calls < 0:
            raise ValueError("llm_calls must be a non-negative integer")
        self.llm_calls = llm_calls


class UnrouteableTaskError(CompilationError):
    """Valid routing judgment that needs a revised Scientific task."""


class CompilerLLM(Protocol):
    def next_action(self, prompt: str, action_type: type[BaseModel]) -> BaseModel | dict: ...


class WorkflowCompiler(Protocol):
    def compile(
        self, request: WorkRequest, *, current: Workflow | None,
        registry: WorkflowAgentRegistry, limits: ExecutionLimits,
        workspaces: list[WorkspaceDescriptor] | None = None,
    ) -> CompilationResult: ...


TaskKey = Annotated[str, StringConstraints(
    min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
)]
NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class TaskRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    task_key: TaskKey
    workflow_agent_kind: WorkflowAgentKind | None = None
    reason: NonEmpty | None = None

    @model_validator(mode="after")
    def validate_choice(self):
        if (self.workflow_agent_kind is None) == (self.reason is None):
            raise ValueError("provide exactly one Agent match or unrouteable reason")
        return self


class RouteTasks(BaseModel):
    """The sole Compiler output: one routing decision per existing task."""
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    routes: list[TaskRoute] = Field(min_length=1)


class DeterministicWorkflowCompiler:
    """Inject a trusted graph in orchestration fixtures without an LLM."""

    def __init__(self, proposal: WorkflowProposal, patch: WorkflowPatch | None = None) -> None:
        self._proposal = proposal
        self._patch = patch

    def compile(self, request, *, current, registry, limits, workspaces=None):
        if current is None:
            proposal = self._proposal.model_copy(deep=True)
            proposal.work_request_id = request.id
            for task in proposal.tasks:
                task.work_request_id = request.id
                task.shared_goal = request.request.objective
                task.shared_constraints = list(request.request.constraints)
            return CompilationResult(proposal)
        if self._patch is None:
            raise CompilationError("no patch configured for an existing workflow")
        patch = self._patch.model_copy(deep=True)
        patch.work_request_id = request.id
        for task in patch.add_tasks:
            task.work_request_id = request.id
            task.shared_goal = request.request.objective
            task.shared_constraints = list(request.request.constraints)
        return CompilationResult(patch)


def _compile_prompt(request, current, registry, limits, workspaces, *, feedback=None):
    lines = [
        "### Responsibility",
        "Match each explicit Scientific task to a registered execution Agent by the task's purpose. "
        "Coding owns implementation and code correctness; Experiment owns research execution and measurements, "
        "including analysis of existing results. Scientific is never a graph node.",
        "You are a router, not a task planner. Do not rewrite instructions or shared constraints, "
        "add, delete, split or merge tasks, alter dependencies, select resources, or broaden permissions. "
        "Local keys identify already specified tasks; runtime identities are assigned by fixed code.",
        "If one task mixes incompatible responsibilities, cannot be matched, or requires an unavailable Agent, "
        "return its specific unrouteable reason. Do not silently choose a fallback or repair the request.",
        "",
        "### Output",
        "Call the only supplied tool route_tasks once. Cover each supplied task key exactly once. "
        "For each key provide workflow_agent_kind, or reason when it cannot be routed; never both. "
        "Do not invent task text, evidence, file paths, commands, or control fields.",
        "",
        "### Available execution Agents",
        *[f"- {item.workflow_agent_kind.value}: {item.description}" for item in registry.definitions],
        "",
        "### Original work input",
        request.request.model_dump_json(),
    ]
    if feedback:
        lines.extend(["", "### Structural feedback", "Previous routing rejected: " + feedback])
    return "\n".join(lines)


def _validate_request_resources(request, current, limits, workspaces):
    used = len(current.tasks) if current else 0
    if len(request.request.tasks) > limits.max_tasks - used:
        raise CompilationError("explicit tasks exceed remaining task limits")
    workspace_ids = {w.workspace_id for w in workspaces}
    for task in request.request.tasks:
        if task.workspace_id is None and len(workspace_ids) > 1:
            raise CompilationError("workspace_id required with multiple workspaces")
        if task.workspace_id is not None and task.workspace_id not in workspace_ids:
            raise CompilationError("task selected an undeclared workspace")


def _materialize_routes(routes, *, request, current, registry, limits, workspaces):
    _validate_request_resources(request, current, limits, workspaces)
    keys = [task.key for task in request.request.tasks]
    route_keys = [route.task_key for route in routes.routes]
    if len(route_keys) != len(set(route_keys)):
        raise CompilationError("routing has duplicate task keys")
    if set(route_keys) != set(keys):
        raise CompilationError("routing must cover exactly the supplied task keys")
    declared = {item.workflow_agent_kind for item in registry.definitions}
    by_key = {route.task_key: route for route in routes.routes}
    for route in routes.routes:
        if route.reason is not None:
            raise UnrouteableTaskError(f"task {route.task_key} cannot be routed: {route.reason}")
        if route.workflow_agent_kind not in declared:
            raise CompilationError("router selected an undeclared Agent")
    existing = {task.id for task in current.tasks} if current else set()
    mapping = {}
    for key in keys:
        base = f"task_{key}"
        candidate = base
        counter = 1
        while candidate in existing:
            candidate = f"{base}_{request.id.removeprefix('work_')}_{counter}"
            counter += 1
        existing.add(candidate)
        mapping[key] = candidate
    workspace_ids = [w.workspace_id for w in workspaces]
    tasks = []
    for item in request.request.tasks:
        workspace = item.workspace_id
        if workspace is None and len(workspace_ids) == 1:
            workspace = workspace_ids[0]
        tasks.append(TaskProposal(
            id=mapping[item.key], work_request_id=request.id,
            workflow_agent_kind=by_key[item.key].workflow_agent_kind,
            instruction=item.instruction,
            shared_goal=request.request.objective,
            shared_constraints=list(request.request.constraints),
            depends_on=[mapping[key] for key in item.depends_on], workspace_id=workspace,
            input_artifacts=list(item.input_artifact_ids),
            input_artifact_bindings=[FutureArtifactBinding(
                source_task=mapping[binding.source_task], output_selector=binding.output_selector,
            ) for binding in item.input_artifact_bindings],
            output_names=list(item.output_names),
        ))
    if current is None:
        candidate = WorkflowProposal(work_request_id=request.id, tasks=tasks)
    else:
        candidate = WorkflowPatch(work_request_id=request.id, based_on_revision=current.revision, add_tasks=tasks)
    validate_workflow_candidate(candidate)
    return candidate


class LLMWorkflowCompiler:
    """One routing call, with at most one correction of invalid output structure."""

    def __init__(self, client: CompilerLLM) -> None:
        self._client = client
        self.llm_calls = 0

    def compile(self, request, *, current, registry, limits, workspaces=None):
        if current_budget() is None:
            raise CompilationError("Compiler requires a caller-supplied execution budget")
        self.llm_calls = 0
        initial_usage = current_budget().usage.used
        feedback = None
        try:
            request = WorkRequest.model_validate(request)
            _validate_request_resources(request, current, limits, workspaces or [])
            for attempt in range(2):
                current_budget().check()
                setter = getattr(self._client, "set_attempt_limit", None)
                if setter:
                    setter(current_budget().remaining_calls)
                tracer = getattr(self._client, "set_trace_context", None)
                if tracer:
                    tracer(agent="workflow_compiler", run_id=request.run_id, work_request_id=request.id)
                try:
                    raw = invoke_model(self._client, "next_action",
                        _compile_prompt(request, current, registry, limits, workspaces or [], feedback=feedback), RouteTasks)
                    routes = RouteTasks.model_validate(raw.model_dump() if isinstance(raw, BaseModel) else raw)
                    output = _materialize_routes(routes, request=request, current=current,
                        registry=registry, limits=limits, workspaces=workspaces or [])
                    return CompilationResult(output, current_budget().usage.used - initial_usage)
                except UnrouteableTaskError:
                    raise
                except (ValueError, json.JSONDecodeError) as error:
                    if attempt:
                        raise CompilationError(f"router failed after 2 attempts: {error}") from error
                    feedback = str(error)
                finally:
                    self.llm_calls = current_budget().usage.used - initial_usage
        except (BudgetExhaustedError, DeadlineExceededError, OperationCancelled):
            raise
        except Exception as error:
            if isinstance(error, CompilationError):
                error.llm_calls = self.llm_calls
                raise
            raise CompilationError(str(error) or type(error).__name__, llm_calls=self.llm_calls) from error
        raise CompilationError("router failed", llm_calls=self.llm_calls)
