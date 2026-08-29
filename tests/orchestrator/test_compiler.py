"""Contract tests for the Phase 7 WorkflowCompiler (DEVELOPMENT_PLAN §7.2).

Covers the "semantic draft + deterministic materialization + bounded recompile"
design (ADR-0010): the LLM returns only a local ``CompilationDraft`` and the
compiler assigns every runtime identity/scope field and emits a schema-valid
``WorkflowProposal`` (first round) or append-only ``WorkflowPatch`` (repair).
"""

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from resagent2_contracts import (
    AgentOwner,
    Capability,
    CapabilityDefinition,
    CapabilityRegistry,
    ExperimentRunInput,
    ModuleResult,
    ModuleStatus,
    ResearchRequest,
    RunBudget,
    Workflow,
    WorkflowPatch,
    WorkflowProposal,
    WorkflowTask,
    WorkRequest,
    WorkRequestDraft,
    WorkspaceDescriptor,
    WorkspaceSourceKind,
)
from resagent2_orchestrator import (
    CompilationError,
    DeterministicWorkflowCompiler,
    InMemoryRunStore,
    LLMWorkflowCompiler,
    ModuleBinding,
    ScriptedModulePort,
    WorkflowScheduler,
)
from resagent2_orchestrator.compiler import (
    CompilationDraft,
    _materialize_draft,
)

NOW = datetime(2026, 8, 28, tzinfo=UTC)

WS_MAIN = WorkspaceDescriptor(workspace_id="ws_main", source_kind=WorkspaceSourceKind.LOCAL)
WS_ALT = WorkspaceDescriptor(
    workspace_id="ws_alt", source_kind=WorkspaceSourceKind.GENERATED
)


def work_request() -> WorkRequest:
    return WorkRequest(
        id="work_round1",
        run_id="run_example",
        scientific_session_id="session_sci",
        request=WorkRequestDraft(
            objective="Measure the method",
            expected_evidence=["validation_accuracy"],
        ),
        created_at=NOW,
        updated_at=NOW,
    )


def registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        definitions=[
            CapabilityDefinition(
                capability=Capability.CODE_MODIFY,
                owner=AgentOwner.CODING,
                request_model="CodeModifyInput",
                result_model="CodeModifyResult",
                permission_policy="read_write_workspace",
                completion_evidence=["code_patch", "code_change"],
            ),
            CapabilityDefinition(
                capability=Capability.EXPERIMENT_RUN,
                owner=AgentOwner.EXPERIMENT,
                request_model="ExperimentRunInput",
                result_model="ExperimentResult",
                permission_policy="read_write_workspace",
                completion_evidence=["experiment_result"],
            ),
        ]
    )


def budget(max_tasks: int = 5) -> RunBudget:
    return RunBudget(
        max_tasks=max_tasks,
        max_attempts_per_task=2,
        max_llm_calls=20,
        timeout_seconds=60,
    )


def proposal(*task_ids: str) -> WorkflowProposal:
    from resagent2_contracts import TaskProposal

    tasks = [
        TaskProposal(
            id=task_id,
            work_request_id="work_round1",
            capability=Capability.EXPERIMENT_RUN,
            goal=f"Run {task_id}",
            rationale="required evidence",
            inputs=ExperimentRunInput(instructions=f"Run {task_id}"),
        )
        for task_id in task_ids
    ]
    return WorkflowProposal(
        work_request_id="work_round1",
        summary="compiled proposal",
        compilation_rationale="semantic translation",
        tasks=tasks,
    )


# --- draft raw-dict helpers --------------------------------------------------


def raw_experiment(key: str = "run") -> dict:
    return {
        "summary": "run the experiment",
        "rationale": "obtain evidence",
        "tasks": [
            {
                "key": key,
                "capability": "experiment_run",
                "goal": f"Run {key}",
                "rationale": "required evidence",
                "inputs": {"capability": "experiment_run", "instructions": f"Run {key}"},
            }
        ],
    }


def raw_repair() -> dict:
    return {
        "summary": "repair",
        "rationale": "fix the bug and rerun",
        "tasks": [
            {
                "key": "fix",
                "capability": "code_modify",
                "goal": "Fix the bug",
                "rationale": "repair",
                "inputs": {"capability": "code_modify", "instructions": "Fix the bug"},
            },
            {
                "key": "rerun",
                "capability": "experiment_run",
                "goal": "Rerun",
                "rationale": "re-obtain evidence",
                "depends_on": ["fix"],
                "inputs": {"capability": "experiment_run", "instructions": "Rerun"},
            },
        ],
    }


def current_workflow() -> Workflow:
    return Workflow(
        run_id="run_example",
        revision=1,
        tasks=[
            WorkflowTask(
                id="task_initial",
                work_request_id="work_1",
                capability=Capability.EXPERIMENT_RUN,
                goal="initial",
                inputs=ExperimentRunInput(instructions="initial"),
            )
        ],
        created_from="work_1",
    )


class _FakeCompilerLLM:
    """Returns one fixed raw dict, recording prompts and target schemas."""

    def __init__(self, raw: dict) -> None:
        self._raw = raw
        self.prompts: list[str] = []
        self.schemas: list[type[BaseModel]] = []

    def next_action(self, prompt: str, action_type: type[BaseModel]) -> dict:
        self.prompts.append(prompt)
        self.schemas.append(action_type)
        return self._raw


class _ScriptedCompilerLLM:
    """Returns a scripted sequence of raw drafts, one per call."""

    def __init__(self, drafts: list[dict]) -> None:
        self._drafts = list(drafts)
        self.prompts: list[str] = []
        self.schemas: list[type[BaseModel]] = []

    def next_action(self, prompt: str, action_type: type[BaseModel]) -> dict:
        self.prompts.append(prompt)
        self.schemas.append(action_type)
        if not self._drafts:
            raise AssertionError("no more scripted drafts")
        return self._drafts.pop(0)


def materialize(raw: dict, *, current=None, workspaces=None, max_tasks: int = 5):
    draft = CompilationDraft.model_validate(raw)
    return _materialize_draft(
        draft,
        request=work_request(),
        current=current,
        registry=registry(),
        budget=budget(max_tasks),
        workspaces=workspaces or [],
    )


# --- DeterministicWorkflowCompiler (unchanged behaviour) ---------------------


def test_deterministic_compiler_returns_proposal_for_new_graph() -> None:
    compiler = DeterministicWorkflowCompiler(proposal("task_experiment"))
    result = compiler.compile(
        work_request(), current=None, registry=registry(), budget=budget()
    )
    assert isinstance(result, WorkflowProposal)
    assert result.work_request_id == "work_round1"


def test_deterministic_compiler_returns_patch_for_existing_graph() -> None:
    patch = WorkflowPatch(
        work_request_id="work_round1",
        based_on_revision=1,
        reason="add repair",
    )
    compiler = DeterministicWorkflowCompiler(proposal("task_experiment"), patch)
    result = compiler.compile(
        work_request(), current=current_workflow(), registry=registry(), budget=budget()
    )
    assert isinstance(result, WorkflowPatch)
    assert result.based_on_revision == 1


def test_deterministic_compiler_requires_patch_for_existing_graph() -> None:
    compiler = DeterministicWorkflowCompiler(proposal("task_experiment"))
    with pytest.raises(CompilationError, match="no patch"):
        compiler.compile(
            work_request(), current=current_workflow(), registry=registry(), budget=budget()
        )


# --- Draft schema (plan #1) --------------------------------------------------


def test_draft_rejects_empty_tasks() -> None:
    with pytest.raises(ValidationError):
        CompilationDraft.model_validate({"summary": "s", "rationale": "r", "tasks": []})


def test_draft_rejects_unknown_top_level_field() -> None:
    # The draft must not carry runtime identity fields (extra="forbid").
    with pytest.raises(ValidationError):
        CompilationDraft.model_validate(
            {
                "summary": "s",
                "rationale": "r",
                "work_request_id": "work_1",
                "tasks": [raw_experiment()["tasks"][0]],
            }
        )


# --- Materializer semantic checks (plan #2-#9) -------------------------------


def test_materialize_rejects_duplicate_keys() -> None:
    raw = raw_experiment("run")
    raw["tasks"] = raw["tasks"] + raw["tasks"]  # two identical keys
    with pytest.raises(CompilationError, match="duplicate task keys"):
        materialize(raw)


def test_materialize_rejects_unknown_dependency() -> None:
    raw = raw_experiment("run")
    raw["tasks"][0]["depends_on"] = ["nope"]
    with pytest.raises(CompilationError, match="unknown key"):
        materialize(raw)


def test_materialize_rejects_cycle() -> None:
    raw = {
        "summary": "cycle",
        "rationale": "bad",
        "tasks": [
            {
                "key": "a",
                "capability": "experiment_run",
                "goal": "A",
                "rationale": "x",
                "depends_on": ["b"],
                "inputs": {"capability": "experiment_run", "instructions": "A"},
            },
            {
                "key": "b",
                "capability": "experiment_run",
                "goal": "B",
                "rationale": "x",
                "depends_on": ["a"],
                "inputs": {"capability": "experiment_run", "instructions": "B"},
            },
        ],
    }
    with pytest.raises(CompilationError, match="cycle"):
        materialize(raw)


def test_materialize_rejects_undeclared_capability() -> None:
    raw = {
        "summary": "inspect",
        "rationale": "understand",
        "tasks": [
            {
                "key": "understand",
                "capability": "code_understand",
                "goal": "Inspect",
                "rationale": "evidence",
                "inputs": {"capability": "code_understand", "question": "Where?"},
            }
        ],
    }
    with pytest.raises(CompilationError, match="undeclared"):
        materialize(raw)


def test_materialize_rejects_capability_input_mismatch() -> None:
    raw = {
        "summary": "mismatch",
        "rationale": "bad",
        "tasks": [
            {
                "key": "bad",
                "capability": "code_modify",
                "goal": "Fix",
                "rationale": "repair",
                "inputs": {"capability": "experiment_run", "instructions": "Run"},
            }
        ],
    }
    with pytest.raises(CompilationError, match="does not match"):
        materialize(raw)


def test_materialize_rejects_over_budget() -> None:
    raw = raw_experiment("a")
    raw["tasks"].append(
        {
            "key": "b",
            "capability": "experiment_run",
            "goal": "B",
            "rationale": "x",
            "inputs": {"capability": "experiment_run", "instructions": "B"},
        }
    )
    with pytest.raises(CompilationError, match="budget"):
        materialize(raw, max_tasks=1)


def test_materialize_autofills_single_workspace() -> None:
    result = materialize(raw_experiment("run"), workspaces=[WS_MAIN])
    assert isinstance(result, WorkflowProposal)
    assert result.tasks[0].workspace_id == "ws_main"


def test_materialize_rejects_missing_workspace() -> None:
    with pytest.raises(CompilationError, match="must declare a workspace_id"):
        materialize(raw_experiment("run"), workspaces=[WS_MAIN, WS_ALT])


# --- Proposal / Patch shape and identity (plan #10-#12) ----------------------


def test_compile_produces_proposal() -> None:
    compiler = LLMWorkflowCompiler(_FakeCompilerLLM(raw_experiment("run")))
    result = compiler.compile(
        work_request(), current=None, registry=registry(), budget=budget()
    )
    assert isinstance(result, WorkflowProposal)
    assert result.work_request_id == "work_round1"
    assert [task.id for task in result.tasks] == ["task_run"]


def test_compile_produces_append_only_patch() -> None:
    compiler = LLMWorkflowCompiler(_FakeCompilerLLM(raw_repair()))
    result = compiler.compile(
        work_request(), current=current_workflow(), registry=registry(), budget=budget()
    )
    assert isinstance(result, WorkflowPatch)
    assert result.based_on_revision == 1
    assert result.supersede_task_ids == []
    assert result.pending_task_updates == []
    assert [task.id for task in result.add_tasks] == ["task_fix", "task_rerun"]


def test_materialize_disambiguates_colliding_key() -> None:
    # Round 2 reuses the same key as a round-1 task; the id is deterministically
    # disambiguated (scoped by request.id) rather than rejected.
    result = materialize(raw_experiment("initial"), current=current_workflow())
    assert isinstance(result, WorkflowPatch)
    assert [task.id for task in result.add_tasks] == ["task_initial_round1"]


def test_materialize_generates_global_ids() -> None:
    result = materialize(raw_repair(), current=current_workflow())
    assert isinstance(result, WorkflowPatch)
    # Global task ids are code-assigned from local keys.
    by_id = {task.id: task for task in result.add_tasks}
    assert set(by_id) == {"task_fix", "task_rerun"}
    # Work request id is bound by code, not taken from the draft.
    assert all(task.work_request_id == "work_round1" for task in result.add_tasks)
    # The local dependency was converted to a global id.
    assert by_id["task_rerun"].depends_on == ["task_fix"]


# --- Bounded recompile (plan #13-#16) ----------------------------------------


def test_retry_recovers_from_empty_draft() -> None:
    llm = _ScriptedCompilerLLM([{"summary": "s", "rationale": "r", "tasks": []}, raw_experiment("run")])
    compiler = LLMWorkflowCompiler(llm)
    result = compiler.compile(
        work_request(), current=None, registry=registry(), budget=budget()
    )
    assert isinstance(result, WorkflowProposal)
    assert len(llm.prompts) == 2
    # The second prompt carries the rejection feedback.
    assert "rejected by the deterministic validator" in llm.prompts[1]


def test_retry_recovers_from_bad_dependency() -> None:
    bad = raw_experiment("run")
    bad["tasks"][0]["depends_on"] = ["nope"]
    llm = _ScriptedCompilerLLM([bad, raw_experiment("run")])
    compiler = LLMWorkflowCompiler(llm)
    result = compiler.compile(
        work_request(), current=None, registry=registry(), budget=budget()
    )
    assert isinstance(result, WorkflowProposal)
    assert len(llm.prompts) == 2


def test_retry_fails_after_two_attempts() -> None:
    bad = {"summary": "s", "rationale": "r", "tasks": []}
    compiler = LLMWorkflowCompiler(_ScriptedCompilerLLM([bad, bad]))
    with pytest.raises(CompilationError, match="2 attempts"):
        compiler.compile(
            work_request(), current=None, registry=registry(), budget=budget()
        )


def test_compile_calls_llm_once_when_valid() -> None:
    llm = _FakeCompilerLLM(raw_experiment("run"))
    compiler = LLMWorkflowCompiler(llm)
    compiler.compile(work_request(), current=None, registry=registry(), budget=budget())
    assert len(llm.prompts) == 1
    # The LLM was asked for a CompilationDraft, not a Proposal/Patch.
    assert llm.schemas[0] is CompilationDraft


def test_compile_rejects_invalid_draft_after_retry() -> None:
    # A structurally invalid draft (missing fields) fails on both attempts.
    compiler = LLMWorkflowCompiler(_FakeCompilerLLM({"summary": "no tasks"}))
    with pytest.raises(CompilationError, match="2 attempts"):
        compiler.compile(
            work_request(), current=None, registry=registry(), budget=budget()
        )


def test_compile_rejects_undeclared_workspace_after_retry() -> None:
    raw = raw_experiment("run")
    raw["tasks"][0]["workspace_id"] = "ws_evil"
    compiler = LLMWorkflowCompiler(_FakeCompilerLLM(raw))
    with pytest.raises(CompilationError, match="2 attempts"):
        compiler.compile(
            work_request(),
            current=None,
            registry=registry(),
            budget=budget(),
            workspaces=[WS_MAIN],
        )


# --- Proposal -> workflow traceability (unchanged) ---------------------------


def test_compiler_proposal_traces_to_workflow_created_from() -> None:
    """The same WorkRequest must be traceable to Workflow.created_from."""
    request = work_request()
    compiled = DeterministicWorkflowCompiler(proposal("task_experiment")).compile(
        request, current=None, registry=registry(), budget=budget()
    )
    engine = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort(
                    [ModuleResult(status=ModuleStatus.COMPLETED, summary="done")]
                ),
            )
        },
        store=InMemoryRunStore(),
    )
    run = engine.create_run(
        request.run_id,
        ResearchRequest(
            goal="Measure",
            budget=RunBudget(
                max_tasks=5,
                max_attempts_per_task=2,
                max_llm_calls=20,
                timeout_seconds=60,
            ),
        ),
        compiled,
    )
    assert run.workflow.created_from == request.id


def test_compiler_proposal_over_budget_is_rejected() -> None:
    engine = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort([]),
            )
        },
        store=InMemoryRunStore(),
    )
    over_budget = proposal("task_1", "task_2", "task_3")
    from resagent2_orchestrator import OrchestrationError

    with pytest.raises(OrchestrationError, match="max_tasks"):
        engine.create_run(
            "run_example",
            ResearchRequest(
                goal="Measure",
                budget=RunBudget(
                    max_tasks=2,
                    max_attempts_per_task=2,
                    max_llm_calls=20,
                    timeout_seconds=60,
                ),
            ),
            over_budget,
        )
