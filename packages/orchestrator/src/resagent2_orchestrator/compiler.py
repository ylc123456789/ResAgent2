"""Compile a work request into a bounded graph of single-mode Agent calls."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from resagent2_runtime.budget import BudgetExhaustedError, DeadlineExceededError, current_budget, invoke_model

from resagent2_contracts import (
    FutureArtifactBinding, ExecutionLimits, TaskProposal, Workflow, WorkflowAgentKind,
    WorkflowAgentRegistry, WorkflowPatch, WorkflowProposal, WorkRequest,
    WorkspaceDescriptor, WorkspaceId,
)
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


class CompilerLLM(Protocol):
    def next_action(self, prompt: str, action_type: type[BaseModel]) -> BaseModel | dict: ...


class WorkflowCompiler(Protocol):
    def compile(
        self, request: WorkRequest, *, current: Workflow | None,
        registry: WorkflowAgentRegistry, limits: ExecutionLimits,
        workspaces: list[WorkspaceDescriptor] | None = None,
    ) -> CompilationResult: ...


DraftKey = Annotated[str, StringConstraints(
    strip_whitespace=True, min_length=1, max_length=80,
    pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
)]
NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DraftArtifactBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_task: DraftKey
    output_selector: NonEmpty


class CompilationTaskDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: DraftKey
    workflow_agent_kind: WorkflowAgentKind
    instruction: NonEmpty
    depends_on: list[DraftKey] = Field(default_factory=list)
    workspace_id: WorkspaceId | None = None
    input_artifacts: list[str] = Field(default_factory=list)
    input_artifact_bindings: list[DraftArtifactBinding] = Field(default_factory=list)
    output_names: list[str] = Field(default_factory=list)


class CompilationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[CompilationTaskDraft] = Field(min_length=1)


class DeterministicWorkflowCompiler:
    """Inject a graph in deterministic tests without an LLM."""

    def __init__(self, proposal: WorkflowProposal, patch: WorkflowPatch | None = None) -> None:
        self._proposal = proposal
        self._patch = patch

    def compile(self, request, *, current, registry, limits, workspaces=None):
        if current is None:
            return CompilationResult(self._proposal)
        if self._patch is None:
            raise CompilationError("no patch configured for an existing workflow")
        return CompilationResult(self._patch)


def _compile_prompt(request, current, registry, limits, workspaces, *, feedback=None):
    remaining = limits.max_tasks - (len(current.tasks) if current else 0)
    lines = [
        "Compile the smallest currently executable graph for this work request.",
        request.request.model_dump_json(),
        "Available execution Agents:",
        *[f"- {item.workflow_agent_kind.value}: {item.description}" for item in registry.definitions],
        "Each Agent has one business mode. Keep its inspection, preparation and execution "
        "inside the same task where possible. Scientific is never a graph node.",
        "Return CompilationDraft. Use instruction as the only task text. Do not invent "
        "metric names, file paths, acceptance policies, permissions or runtime identities.",
        "Dependencies refer only to keys in this draft and require success. "
        "Conditional repairs are requested in a later round after an actual failure.",
        "Future input bindings select a logical output_name declared by a direct dependency. "
        "Use output_names only when an explicit cross-task handoff needs named outputs. "
        "Do not guess artifact IDs. Existing input_artifacts must already be supplied materials.",
        f"Remaining tasks: {remaining}",
        "Logical workspaces: " + json.dumps([w.model_dump(mode="json") for w in workspaces]),
        "Draft schema: " + json.dumps(CompilationDraft.model_json_schema()),
    ]
    if feedback:
        lines.append("Previous draft rejected by structural validation: " + feedback)
    return "\n".join(lines)


def _materialize_draft(draft, *, request, current, registry, limits, workspaces):
    used = len(current.tasks) if current else 0
    if not draft.tasks or len(draft.tasks) > limits.max_tasks - used:
        raise CompilationError("draft exceeds remaining task limits or is empty")
    keys = [task.key for task in draft.tasks]
    if len(keys) != len(set(keys)):
        raise CompilationError("draft has duplicate task keys")
    declared = {item.workflow_agent_kind for item in registry.definitions}
    workspace_ids = [w.workspace_id for w in workspaces]
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
    tasks = []
    for item in draft.tasks:
        if item.workflow_agent_kind not in declared:
            raise CompilationError("compiler selected an undeclared Agent")
        if not set(item.input_artifacts) <= set(request.request.input_artifact_ids):
            raise CompilationError("compiler selected an unsupplied input artifact")
        if any(dep not in mapping for dep in item.depends_on):
            raise CompilationError("draft dependency references an unknown key")
        workspace = item.workspace_id
        if workspace is None and len(workspace_ids) == 1:
            workspace = workspace_ids[0]
        elif workspace is None and len(workspace_ids) > 1:
            raise CompilationError("workspace_id required with multiple workspaces")
        if workspace is not None and workspace not in workspace_ids:
            raise CompilationError("draft selected an undeclared workspace")
        bindings = []
        for binding in item.input_artifact_bindings:
            if binding.source_task not in mapping:
                raise CompilationError("future artifact source is unknown")
            bindings.append(FutureArtifactBinding(
                source_task=mapping[binding.source_task], output_selector=binding.output_selector,
            ))
        tasks.append(TaskProposal(
            id=mapping[item.key], work_request_id=request.id,
            workflow_agent_kind=item.workflow_agent_kind, instruction=item.instruction,
            depends_on=[mapping[key] for key in item.depends_on], workspace_id=workspace,
            input_artifacts=item.input_artifacts, input_artifact_bindings=bindings,
            output_names=item.output_names,
        ))
    if current is None:
        candidate = WorkflowProposal(work_request_id=request.id, tasks=tasks)
    else:
        candidate = WorkflowPatch(
            work_request_id=request.id, based_on_revision=current.revision, add_tasks=tasks,
        )
    validate_workflow_candidate(candidate)
    return candidate


class LLMWorkflowCompiler:
    """One structured draft, with at most one correction for invalid structure."""

    def __init__(self, client: CompilerLLM) -> None:
        self._client = client
        self.llm_calls = 0

    def compile(self, request, *, current, registry, limits, workspaces=None):
        if current_budget() is None:
            raise CompilationError("Compiler requires a caller-supplied execution budget")
        return self._compile(request, current=current, registry=registry, limits=limits,
                             workspaces=workspaces)

    def _compile(self, request, *, current, registry, limits, workspaces=None):
        self.llm_calls = 0
        initial_usage = current_budget().usage.used
        feedback = None
        try:
            if limits.max_tasks <= (len(current.tasks) if current else 0):
                raise CompilationError("no remaining task slots")
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
                        _compile_prompt(request, current, registry, limits, workspaces or [], feedback=feedback),
                        CompilationDraft,
                    )
                except (json.JSONDecodeError, ValidationError) as error:
                    if attempt:
                        raise
                    feedback = str(error)
                    continue
                finally:
                    self.llm_calls = current_budget().usage.used - initial_usage
                try:
                    draft = CompilationDraft.model_validate(raw.model_dump() if isinstance(raw, BaseModel) else raw)
                    output = _materialize_draft(
                        draft, request=request, current=current, registry=registry,
                        limits=limits, workspaces=workspaces or [],
                    )
                    return CompilationResult(output, self.llm_calls)
                except (ValueError, json.JSONDecodeError) as error:
                    if attempt:
                        raise CompilationError(f"compiler failed after 2 attempts: {error}") from error
                    feedback = str(error)
        except (BudgetExhaustedError, DeadlineExceededError):
            raise
        except Exception as error:
            if isinstance(error, CompilationError):
                error.llm_calls = self.llm_calls
                raise
            raise CompilationError(str(error) or type(error).__name__, llm_calls=self.llm_calls) from error
        raise CompilationError("compiler failed", llm_calls=self.llm_calls)
