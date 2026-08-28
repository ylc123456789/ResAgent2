"""WorkflowCompiler: translate a semantic WorkRequest into an executable graph.

The compiler is a stateless orchestrator-internal Port. It never persists,
runs tools, calls an Agent, or mutates run state; it only turns one persisted
WorkRequest into a WorkflowProposal (no graph yet) or a WorkflowPatch (an
existing graph). Validation of DAG/capability/budget/revision stays in the
deterministic validator and scheduler, not here.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ValidationError

from resagent2_contracts import (
    CapabilityRegistry,
    RunBudget,
    Workflow,
    WorkflowPatch,
    WorkflowProposal,
    WorkRequest,
    WorkspaceDescriptor,
)


class WorkflowCompiler(Protocol):
    """Translate one WorkRequest into a Proposal or a Patch."""

    def compile(
        self,
        request: WorkRequest,
        *,
        current: Workflow | None,
        registry: CapabilityRegistry,
        budget: RunBudget,
        workspaces: list[WorkspaceDescriptor] | None = None,
    ) -> WorkflowProposal | WorkflowPatch:
        """Return a Proposal when ``current`` is None, else a Patch."""


class CompilationError(ValueError):
    """Raised when a compiler cannot produce a schema-valid graph."""


class CompilerLLM(Protocol):
    """Provider-neutral seam for one bounded structured call.

    The orchestrator must not import ``resagent2_runtime``, so the composition
    root adapts a real runtime LLM client to this shape.
    """

    def next_action(
        self,
        prompt: str,
        action_type: type[BaseModel],
    ) -> dict:
        """Return one JSON dict matching ``action_type``'s schema."""


class DeterministicWorkflowCompiler:
    """Test fixture that returns a fixed proposal, and optionally a fixed patch."""

    def __init__(
        self,
        proposal: WorkflowProposal,
        patch: WorkflowPatch | None = None,
    ) -> None:
        self._proposal = proposal
        self._patch = patch

    def compile(
        self,
        request: WorkRequest,
        *,
        current: Workflow | None,
        registry: CapabilityRegistry,
        budget: RunBudget,
        workspaces: list[WorkspaceDescriptor] | None = None,
    ) -> WorkflowProposal | WorkflowPatch:
        if current is None:
            return self._proposal
        if self._patch is None:
            raise CompilationError("no patch configured for an existing workflow")
        return self._patch


def _compile_prompt(
    request: WorkRequest,
    current: Workflow | None,
    registry: CapabilityRegistry,
    budget: RunBudget,
    workspaces: list[WorkspaceDescriptor] | None,
) -> str:
    """Describe the work request and execution constraints for one LLM call."""
    capabilities = ", ".join(
        item.capability.value for item in registry.definitions
    )
    lines = [
        "Translate this research work request into an executable task graph.",
        f"Objective: {request.request.objective}",
        f"Expected evidence: {', '.join(request.request.expected_evidence)}",
        f"Constraints: {', '.join(request.request.constraints) or '(none)'}",
        f"Available capabilities: {capabilities or '(none)'}",
        "Use only capabilities from the list above; do not invent new ones.",
        f"Max tasks: {budget.max_tasks}",
    ]
    if workspaces:
        descriptions = "; ".join(
            f"{item.workspace_id} ({item.source_kind.value}"
            + (f": {item.description}" if item.description else "")
            + ")"
            for item in workspaces
        )
        lines.append(f"Available workspaces: {descriptions}")
        lines.append(
            "Assign each task a workspace_id from the list above; do not invent ids."
        )
    if current is not None:
        existing = ", ".join(task.id for task in current.tasks)
        lines.append(f"Current workflow revision {current.revision}: {existing or '(empty)'}")
        lines.append("Return a patch that only adds, supersedes, or updates pending tasks.")
    return "\n".join(lines)


def _bind_work_request_id(raw: dict, request: WorkRequest) -> dict:
    """Force the graph's work_request_id onto the request, not the LLM output.

    The LLM may invent a wrong id; traceability from WorkRequest to
    Workflow.created_from is a code guarantee, not a prompt suggestion.
    """
    bound = dict(raw)
    bound["work_request_id"] = request.id
    for key in ("tasks", "add_tasks"):
        for task in bound.get(key, []):
            if isinstance(task, dict):
                task["work_request_id"] = request.id
    return bound


def _reject_undeclared_capabilities(
    proposal: WorkflowProposal | WorkflowPatch,
    registry: CapabilityRegistry,
) -> None:
    """Reject any task whose capability the registry does not declare.

    The LLM may invent a capability not bound by the composition root. That is
    a code guarantee, not a prompt suggestion: compilation fails loudly instead
    of surfacing a confusing "no ModulePort binding" at scheduler acceptance.
    """
    declared = {definition.capability for definition in registry.definitions}
    tasks = proposal.tasks if isinstance(proposal, WorkflowProposal) else proposal.add_tasks
    undeclared = sorted(
        {task.capability.value for task in tasks if task.capability not in declared}
    )
    if undeclared:
        raise CompilationError(
            "compiler selected undeclared capabilities: " + ", ".join(undeclared)
        )


def _reject_undeclared_workspaces(
    proposal: WorkflowProposal | WorkflowPatch,
    workspace_ids: set[str],
) -> None:
    """Reject any task whose workspace_id the composition root did not declare.

    The LLM must only choose from the given logical workspace ids, never invent
    one. Auto-fill for a single workspace happens later, in the scheduler.
    """
    tasks = proposal.tasks if isinstance(proposal, WorkflowProposal) else proposal.add_tasks
    undeclared = sorted(
        {
            task.workspace_id
            for task in tasks
            if task.workspace_id is not None and task.workspace_id not in workspace_ids
        }
    )
    if undeclared:
        raise CompilationError(
            "compiler selected undeclared workspace_ids: " + ", ".join(undeclared)
        )


class LLMWorkflowCompiler:
    """Compile via one bounded structured LLM call, then schema-validate."""

    def __init__(self, client: CompilerLLM) -> None:
        self._client = client

    def compile(
        self,
        request: WorkRequest,
        *,
        current: Workflow | None,
        registry: CapabilityRegistry,
        budget: RunBudget,
        workspaces: list[WorkspaceDescriptor] | None = None,
    ) -> WorkflowProposal | WorkflowPatch:
        workspaces = workspaces or []
        prompt = _compile_prompt(request, current, registry, budget, workspaces)
        if current is None:
            action_type: type[BaseModel] = WorkflowProposal
        else:
            action_type = WorkflowPatch
        raw = self._client.next_action(prompt, action_type)
        raw = _bind_work_request_id(raw, request)
        try:
            proposal = action_type.model_validate(raw)
        except ValidationError as error:
            raise CompilationError(
                f"compiler produced an invalid {action_type.__name__}: {error}"
            ) from error
        _reject_undeclared_capabilities(proposal, registry)
        if workspaces:
            _reject_undeclared_workspaces(
                proposal, {item.workspace_id for item in workspaces}
            )
        return proposal
