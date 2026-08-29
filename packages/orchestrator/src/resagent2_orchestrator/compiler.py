"""WorkflowCompiler: turn a semantic WorkRequest into an executable graph.

The compiler is a stateless orchestrator-internal Port. It never persists, runs
tools, calls an Agent, or mutates run state; it only turns one persisted
WorkRequest into a WorkflowProposal (no graph yet) or a WorkflowPatch (an
existing graph).

The production ``LLMWorkflowCompiler`` follows a "semantic draft + deterministic
materialization" split (ADR-0010):

1. The LLM returns only a local ``CompilationDraft``: semantic task keys, the
   relationships between them, and the capability-specific inputs. It never
   emits a global task id, a work request id, a workflow revision, a status, an
   attempt, or any reference to a task from a previous work request.
2. ``_materialize_draft`` deterministically assigns global task ids, binds the
   work request id, resolves workspaces, converts local dependencies into global
   ones, and emits a schema-valid Proposal (first round) or an append-only Patch
   (repair rounds).
3. If the deterministic validator rejects a draft, exactly one recompilation is
   attempted with the precise rejection reason as feedback; a second rejection
   fails the compile.

The older ``_reject_*`` validators remain as a final defense layer and are still
run over the materialized output (the scheduler keeps its own equivalent checks,
so a compiler can never smuggle a bad graph past it).
"""

from __future__ import annotations

from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from resagent2_contracts import (
    Capability,
    CapabilityInput,
    CapabilityRegistry,
    RunBudget,
    TaskProposal,
    Workflow,
    WorkflowPatch,
    WorkflowProposal,
    WorkRequest,
    WorkspaceDescriptor,
    WorkspaceId,
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


# A local draft key must be a valid suffix of a global ``TaskId``
# (``task_<key>``). It is unique only within the single LLM output it belongs to.
DraftKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    ),
]
NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CompilationTaskDraft(BaseModel):
    """One semantic task as the LLM describes it, before any identity is bound.

    ``key`` is meaningful only inside its ``CompilationDraft``; ``depends_on``
    may only reference other keys in the same draft and means "the referenced
    task must complete successfully before this one may run".
    """

    model_config = ConfigDict(extra="forbid")

    key: DraftKey
    capability: Capability
    goal: NonEmpty
    rationale: NonEmpty
    depends_on: list[DraftKey] = Field(default_factory=list)
    workspace_id: WorkspaceId | None = None
    inputs: CapabilityInput


class CompilationDraft(BaseModel):
    """The only output the Compiler LLM is asked to produce.

    Deliberately carries no execution-graph identity: no global task ids, no
    work request id, no revision, no status, no reference to prior work. All of
    that is assigned by ``_materialize_draft``.
    """

    model_config = ConfigDict(extra="forbid")

    summary: NonEmpty
    rationale: NonEmpty
    tasks: list[CompilationTaskDraft] = Field(min_length=1)


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
    *,
    feedback: str | None = None,
) -> str:
    """Describe the work request and the draft contract for one LLM call.

    The prompt asks only for a ``CompilationDraft``. It never exposes existing
    task ids or the workflow revision to the LLM: those are immutable history
    and are bound by ``_materialize_draft``, not chosen by the model.
    """
    lines = [
        "Translate this research work request into the MINIMAL executable task graph.",
        f"Objective: {request.request.objective}",
        f"Expected evidence: {', '.join(request.request.expected_evidence)}",
        f"Constraints: {', '.join(request.request.constraints) or '(none)'}",
        "Available capabilities:",
    ]
    for item in registry.definitions:
        suffix = f" — {item.description}" if item.description else ""
        lines.append(f"- {item.capability.value}{suffix}")
    lines.extend(
        [
            "Use only capabilities from the list above; do not invent new ones.",
            "Generate the smallest graph that satisfies the request. Do not split "
            "one agent's internal work into separate tasks: code_modify already "
            "reads and diagnoses the code before editing, and experiment_run "
            "already prepares the environment before running.",
            "Every prerequisite the request explicitly names must become a task in "
            "the graph, ordered via dependencies before the evidence-producing tasks.",
            "",
            "Return a JSON draft with this exact shape:",
            "{",
            '  "summary": "<one-line summary>",',
            '  "rationale": "<why this graph>",',
            '  "tasks": [',
            "    {",
            '      "key": "<short snake_case id, unique within this draft>",',
            '      "capability": "<one of the capabilities above>",',
            '      "goal": "<what this task does>",',
            '      "rationale": "<why this task is needed>",',
            '      "depends_on": ["<another key in this draft>", ...],',
            '      "workspace_id": "<only if multiple workspaces; omit for one>",',
            '      "inputs": {"capability": "<same as above>", ...capability-specific fields}',
            "    }",
            "  ]",
            "}",
            "",
            "Do NOT emit a global task id, a work request id, a workflow revision, a "
            "status, an attempt, or any reference to a task from a previous work "
            "request. The system assigns those.",
        ]
    )
    if current is not None:
        remaining = max(0, budget.max_tasks - len(current.tasks))
        lines.append(f"Remaining task budget (new tasks): {remaining}")
    else:
        lines.append(f"Max tasks: {budget.max_tasks}")
    if workspaces:
        descriptions = "; ".join(
            f"{item.workspace_id} ({item.source_kind.value}"
            + (f": {item.description}" if item.description else "")
            + ")"
            for item in workspaces
        )
        lines.append(f"Available workspaces: {descriptions}")
        lines.append(
            "Assign each task a workspace_id from the list above only when more "
            "than one workspace exists; never invent ids."
        )
    if current is not None:
        lines.append(
            "The workflow already exists. This is an append-only round: existing "
            "tasks are immutable history and must not be referenced. Only add NEW "
            "tasks for this work request; a new task may only depend on other new "
            "tasks in this same draft."
        )
    if feedback is not None:
        lines.append("")
        lines.append(feedback)
    return "\n".join(lines)


def _reject_cycle(keys: list[str], dependencies: dict[str, list[str]]) -> None:
    """Reject a dependency cycle among a draft's local keys."""

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise CompilationError("draft dependency cycle detected")
        if key in visited:
            return
        visiting.add(key)
        for dependency in dependencies[key]:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in keys:
        visit(key)


def _materialize_draft(
    draft: CompilationDraft,
    *,
    request: WorkRequest,
    current: Workflow | None,
    registry: CapabilityRegistry,
    budget: RunBudget,
    workspaces: list[WorkspaceDescriptor],
) -> WorkflowProposal | WorkflowPatch:
    """Deterministically bind identity and scope onto a semantic draft.

    The LLM decided *what* to do and *how tasks relate*; this function decides
    every runtime identity and scope field, and rejects any draft that is not a
    valid, minimal, self-contained task graph.
    """
    # 1. At least one task (the schema already enforces this; kept as a guard).
    if not draft.tasks:
        raise CompilationError("draft has no tasks")

    # 2. Task count within the remaining budget.
    existing = len(current.tasks) if current is not None else 0
    remaining = budget.max_tasks - existing
    if len(draft.tasks) > remaining:
        raise CompilationError(
            f"draft has {len(draft.tasks)} tasks but only {remaining} remain "
            "in the task budget"
        )

    # 3. Local keys are unique.
    keys = [task.key for task in draft.tasks]
    if len(keys) != len(set(keys)):
        raise CompilationError("draft has duplicate task keys")

    # 4. Dependencies only reference this draft's keys.
    key_set = set(keys)
    for task in draft.tasks:
        foreign = [dep for dep in task.depends_on if dep not in key_set]
        if foreign:
            raise CompilationError(
                f"task {task.key!r} depends on unknown key(s): {', '.join(foreign)}"
            )

    # 5. The local dependency graph is acyclic.
    _reject_cycle(keys, {task.key: task.depends_on for task in draft.tasks})

    # 6. Every capability is declared by the composition root.
    declared = {definition.capability for definition in registry.definitions}
    undeclared = sorted(
        {task.capability.value for task in draft.tasks if task.capability not in declared}
    )
    if undeclared:
        raise CompilationError(
            "compiler selected undeclared capabilities: " + ", ".join(undeclared)
        )

    # 7. The capability matches its inputs discriminator.
    for task in draft.tasks:
        if task.capability != task.inputs.capability:
            raise CompilationError(
                f"task {task.key!r} capability {task.capability.value!r} does not "
                f"match inputs {task.inputs.capability.value!r}"
            )

    # 8. Resolve workspaces (auto-fill a single one, require a choice for many).
    workspace_ids = [item.workspace_id for item in workspaces]
    resolved: list[str | None] = []
    for task in draft.tasks:
        if task.workspace_id is None:
            if len(workspace_ids) == 1:
                resolved.append(workspace_ids[0])
            elif len(workspace_ids) > 1:
                raise CompilationError(
                    f"task {task.key!r} must declare a workspace_id "
                    "(multiple workspaces exist)"
                )
            else:
                resolved.append(None)
        elif task.workspace_id in workspace_ids:
            resolved.append(task.workspace_id)
        else:
            raise CompilationError(
                f"task {task.key!r} declares undeclared workspace_id "
                f"{task.workspace_id!r}"
            )

    # 9. Assign deterministic global task ids. A key that collides with an
    # existing workflow task is disambiguated deterministically by scoping it to
    # this work request, so reusing a key across rounds can never fail the run:
    # the LLM never sees old task ids (ADR-0010 §4), so it cannot be expected to
    # avoid their keys.
    existing_ids = {task.id for task in current.tasks} if current is not None else set()
    key_to_id: dict[str, str] = {}
    for task in draft.tasks:
        base = f"task_{task.key}"
        candidate = base
        if candidate in existing_ids:
            scope = request.id.removeprefix("work_")
            candidate = f"{base}_{scope}"
            index = 2
            while candidate in existing_ids:
                candidate = f"{base}_{scope}_{index}"
                index += 1
        key_to_id[task.key] = candidate
        existing_ids.add(candidate)

    # 10-12. Convert local dependencies to global ids and emit the contract.
    proposals = [
        TaskProposal(
            id=key_to_id[task.key],
            work_request_id=request.id,
            capability=task.capability,
            goal=task.goal,
            rationale=task.rationale,
            depends_on=[key_to_id[dep] for dep in task.depends_on],
            workspace_id=workspace,
            inputs=task.inputs,
        )
        for task, workspace in zip(draft.tasks, resolved)
    ]
    if current is None:
        return WorkflowProposal(
            work_request_id=request.id,
            summary=draft.summary,
            tasks=proposals,
            compilation_rationale=draft.rationale,
        )
    return WorkflowPatch(
        work_request_id=request.id,
        based_on_revision=current.revision,
        reason=draft.rationale,
        add_tasks=proposals,
    )


def _reject_undeclared_capabilities(
    proposal: WorkflowProposal | WorkflowPatch,
    registry: CapabilityRegistry,
) -> None:
    """Reject any task whose capability the registry does not declare.

    Kept as a final defense over the materialized output (ADR-0010 §5); the
    deterministic materializer already enforces this, and the scheduler has its
    own binding check.
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


def _reject_cross_request_mutations(patch: WorkflowPatch) -> None:
    """Reject supersede/update of existing tasks from a new WorkRequest.

    The compiler is only ever invoked for a WorkRequest whose tasks are not yet
    in the graph, so the existing tasks belong to previous work requests and are
    immutable history. Surfacing this here keeps the error out of the scheduler.
    """
    if patch.supersede_task_ids or patch.pending_task_updates:
        raise CompilationError(
            "patch must only add tasks; supersede/update of existing tasks is forbidden"
        )


def _reject_empty_graph(proposal: WorkflowProposal | WorkflowPatch) -> None:
    """Reject a graph with no tasks: a WorkRequest must produce at least one."""
    tasks = proposal.tasks if isinstance(proposal, WorkflowProposal) else proposal.add_tasks
    if not tasks:
        raise CompilationError("compiler produced an empty task graph")


def _reject_cross_request_dependencies(patch: WorkflowPatch) -> None:
    """Reject add_tasks that depend on a task outside the patch.

    Existing workflow tasks are immutable history (completed or failed); a new
    WorkRequest's tasks may only depend on other tasks it adds itself, never on
    a prior task (a failed prior task would make the new task forever blocked).
    """
    add_ids = {task.id for task in patch.add_tasks}
    for task in patch.add_tasks:
        foreign = [dep for dep in task.depends_on if dep not in add_ids]
        if foreign:
            raise CompilationError(
                f"add task {task.id} depends on tasks outside the patch: "
                + ", ".join(foreign)
            )


def _reject_undeclared_workspaces(
    proposal: WorkflowProposal | WorkflowPatch,
    workspace_ids: set[str],
) -> None:
    """Reject any task whose workspace_id the composition root did not declare.

    The LLM must only choose from the given logical workspace ids, never invent
    one. Auto-fill for a single workspace happens in the materializer.
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


def _compact_error(error: Exception) -> str:
    """Render a validator failure as a short, actionable one-liner for feedback."""
    if isinstance(error, ValidationError):
        details = [
            ".".join(str(part) for part in item["loc"]) + ": " + item["msg"]
            for item in error.errors(include_url=False)
        ]
        return "; ".join(details[:5])
    return str(error)


class LLMWorkflowCompiler:
    """Compile a WorkRequest through a semantic draft, then materialize it.

    The LLM produces a local ``CompilationDraft``; ``_materialize_draft``
    deterministically assigns global identity and scope and emits a valid
    Proposal or append-only Patch. On validator rejection, one bounded
    recompilation is attempted with the exact reason as feedback; a second
    rejection fails the compile (CONTRACTS §20.9).
    """

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
        feedback: str | None = None
        for attempt in (0, 1):
            prompt = _compile_prompt(
                request, current, registry, budget, workspaces, feedback=feedback
            )
            raw = self._client.next_action(prompt, CompilationDraft)
            try:
                draft = CompilationDraft.model_validate(raw)
                compiled = _materialize_draft(
                    draft,
                    request=request,
                    current=current,
                    registry=registry,
                    budget=budget,
                    workspaces=workspaces,
                )
            except (ValidationError, CompilationError) as error:
                if attempt == 1:
                    raise CompilationError(
                        "compiler failed after 2 attempts: " + _compact_error(error)
                    ) from error
                feedback = (
                    "The previous draft was rejected by the deterministic validator.\n"
                    f"Reason: {_compact_error(error)}\n"
                    "Return a corrected draft."
                )
                continue

            # Final defense over the materialized output (ADR-0010 §5). These are
            # all guaranteed by _materialize_draft, but kept so a future change to
            # the materializer cannot silently bypass them.
            _reject_undeclared_capabilities(compiled, registry)
            _reject_empty_graph(compiled)
            if isinstance(compiled, WorkflowPatch):
                _reject_cross_request_mutations(compiled)
                _reject_cross_request_dependencies(compiled)
            if workspaces:
                _reject_undeclared_workspaces(
                    compiled, {item.workspace_id for item in workspaces}
                )
            return compiled

        # Unreachable: attempt 1 always returns or raises.
        raise CompilationError("compiler failed after 2 attempts")
