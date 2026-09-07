"""Contract tests for the Phase 7 WorkflowCompiler (DEVELOPMENT_PLAN §7.2).

Covers the "semantic draft + deterministic materialization + bounded recompile"
design (ADR-0010): the LLM returns only a local ``CompilationDraft`` and the
compiler assigns every runtime identity/scope field and emits a schema-valid
``WorkflowProposal`` (first round) or append-only ``WorkflowPatch`` (repair).
"""

from datetime import UTC, datetime
import json

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
    RunStatus,
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
    ResearchRun,
    ScriptedModulePort,
    WorkflowScheduler,
)
from resagent2_orchestrator.compiler import (
    CompilationDraft,
    CompilationReview,
    _materialize_draft,
)

NOW = datetime(2026, 8, 28, tzinfo=UTC)


def _create_run(engine, run_id, request, proposal):
    now = datetime.now(UTC)
    engine.store.save(
        ResearchRun(
            run_id=run_id,
            request=request,
            status=RunStatus.RUNNING,
            created_at=now,
            updated_at=now,
        )
    )
    return engine.accept_proposal(run_id, proposal)

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
            ),
            CapabilityDefinition(
                capability=Capability.EXPERIMENT_RUN,
                owner=AgentOwner.EXPERIMENT,
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
                "inputs": {"capability": "code_modify", "instructions": "Fix the bug"},
            },
            {
                "key": "rerun",
                "capability": "experiment_run",
                "goal": "Rerun",
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
    """Returns one fixed raw draft and accepts the semantic review by default."""

    def __init__(self, raw: dict, review: dict | None = None) -> None:
        self._raw = raw
        self._review = review if review is not None else {"accepted": True}
        self.prompts: list[str] = []
        self.schemas: list[type[BaseModel]] = []

    def next_action(self, prompt: str, action_type: type[BaseModel]) -> dict:
        self.prompts.append(prompt)
        self.schemas.append(action_type)
        if action_type is CompilationReview:
            return self._review
        return self._raw


class _ScriptedCompilerLLM:
    """Returns scripted drafts (and optional reviews), keyed by action type."""

    def __init__(self, drafts: list[dict], reviews: list[dict] | None = None) -> None:
        self._drafts = list(drafts)
        self._reviews = list(reviews) if reviews is not None else []
        self.prompts: list[str] = []
        self.schemas: list[type[BaseModel]] = []

    def next_action(self, prompt: str, action_type: type[BaseModel]) -> dict:
        self.prompts.append(prompt)
        self.schemas.append(action_type)
        if action_type is CompilationReview:
            if self._reviews:
                return self._reviews.pop(0)
            return {"accepted": True}
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
    ).output
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
    ).output
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
                "depends_on": ["b"],
                "inputs": {"capability": "experiment_run", "instructions": "A"},
            },
            {
                "key": "b",
                "capability": "experiment_run",
                "goal": "B",
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
    ).output
    assert isinstance(result, WorkflowProposal)
    assert result.work_request_id == "work_round1"
    assert [task.id for task in result.tasks] == ["task_run"]


def test_shared_candidate_rejection_enters_bounded_recompile(monkeypatch) -> None:
    import resagent2_orchestrator.compiler as compiler_module

    materialize = compiler_module._materialize_draft
    materializations = 0

    def damage_first_candidate(*args, **kwargs):
        nonlocal materializations
        materializations += 1
        candidate = materialize(*args, **kwargs)
        if materializations == 1:
            return candidate.model_copy(update={"tasks": []})
        return candidate

    monkeypatch.setattr(compiler_module, "_materialize_draft", damage_first_candidate)
    client = _FakeCompilerLLM(raw_experiment("run"))
    result = LLMWorkflowCompiler(client).compile(
        work_request(), current=None, registry=registry(), budget=budget(),
    )

    assert materializations == 2
    assert result.llm_calls == 3  # rejected draft, corrected draft, accepted review
    assert "empty task graph" in client.prompts[1]
    assert [task.id for task in result.output.tasks] == ["task_run"]


@pytest.mark.parametrize("damaged_type", [CompilationDraft, CompilationReview])
def test_typed_compiler_candidates_are_revalidated(damaged_type) -> None:
    draft = CompilationDraft.model_validate(raw_experiment())
    review = CompilationReview(accepted=True)
    if damaged_type is CompilationDraft:
        draft = draft.model_copy(update={"tasks": []})
    else:
        # A nonempty string must not be accepted as a truthy review verdict.
        review = review.model_copy(update={"accepted": "false"})
    compiler = LLMWorkflowCompiler(_FakeCompilerLLM(draft, review=review))

    with pytest.raises(CompilationError, match="2 attempts") as error:
        compiler.compile(
            work_request(), current=None, registry=registry(), budget=budget(),
        )
    assert error.value.llm_calls == (2 if damaged_type is CompilationDraft else 4)


def test_compile_produces_append_only_patch() -> None:
    compiler = LLMWorkflowCompiler(_FakeCompilerLLM(raw_repair()))
    result = compiler.compile(
        work_request(), current=current_workflow(), registry=registry(), budget=budget()
    ).output
    assert isinstance(result, WorkflowPatch)
    assert result.based_on_revision == 1
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
    ).output
    assert isinstance(result, WorkflowProposal)
    assert len(llm.prompts) == 3  # draft[bad], draft[corrected], review
    # The second prompt carries the rejection feedback.
    assert "rejected by the deterministic validator" in llm.prompts[1]


def test_compiler_stops_at_remaining_calls() -> None:
    """The compiler must not make a 6th call when only 5 are budgeted."""
    from resagent2_orchestrator.compiler import CompilationReview

    class _CountingLLM:
        def __init__(self) -> None:
            self.calls = 0

        def next_action(self, prompt, action_type):
            self.calls += 1
            if action_type is CompilationReview:
                return {"accepted": True}
            return raw_experiment("run")

    llm = _CountingLLM()
    compiler = LLMWorkflowCompiler(llm)
    with pytest.raises(CompilationError, match="budget exhausted"):
        compiler.compile(
            work_request(),
            current=None,
            registry=registry(),
            budget=budget(),
            remaining_calls=1,
        )
    assert llm.calls == 1


def test_compiler_passes_decreasing_attempt_budget_to_client() -> None:
    class BudgetAwareCompilerLLM:
        def __init__(self) -> None:
            self.attempt_limits: list[int] = []
            self.last_attempts = 1

        def set_attempt_limit(self, max_attempts: int) -> None:
            self.attempt_limits.append(max_attempts)

        def next_action(self, prompt, action_type):
            if action_type is CompilationReview:
                return {"accepted": True}
            return raw_experiment("run")

    llm = BudgetAwareCompilerLLM()
    LLMWorkflowCompiler(llm).compile(
        work_request(),
        current=None,
        registry=registry(),
        budget=budget(),
        remaining_calls=2,
    )
    assert llm.attempt_limits == [2, 1]


def test_retry_recovers_from_bad_dependency() -> None:
    bad = raw_experiment("run")
    bad["tasks"][0]["depends_on"] = ["nope"]
    llm = _ScriptedCompilerLLM([bad, raw_experiment("run")])
    compiler = LLMWorkflowCompiler(llm)
    result = compiler.compile(
        work_request(), current=None, registry=registry(), budget=budget()
    ).output
    assert isinstance(result, WorkflowProposal)
    assert len(llm.prompts) == 3  # draft[bad], draft[corrected], review


def test_retry_fails_after_two_attempts() -> None:
    bad = {"summary": "s", "rationale": "r", "tasks": []}
    compiler = LLMWorkflowCompiler(_ScriptedCompilerLLM([bad, bad]))
    with pytest.raises(CompilationError, match="2 attempts"):
        compiler.compile(
            work_request(), current=None, registry=registry(), budget=budget()
        )


def test_compile_calls_llm_twice_when_valid() -> None:
    llm = _FakeCompilerLLM(raw_experiment("run"))
    compiler = LLMWorkflowCompiler(llm)
    compiler.compile(work_request(), current=None, registry=registry(), budget=budget())
    # One draft call + one semantic review call.
    assert len(llm.prompts) == 2  # one draft call + one review call
    assert llm.schemas[0] is CompilationDraft
    assert llm.schemas[1] is CompilationReview


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
    ).output
    engine = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=ScriptedModulePort(
                    [ModuleResult(status=ModuleStatus.COMPLETED, summary="done", payload={"env_id": "resenv_test"})]
                ),
            )
        },
        store=InMemoryRunStore(),
    )
    run = _create_run(engine,
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
        _create_run(engine,
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


# --- Compiler boundary: no fabricated code details + semantic review ---------


def test_materialize_strips_suggested_paths_for_code_modify() -> None:
    raw = {
        "summary": "implement",
        "rationale": "needed",
        "tasks": [
            {
                "key": "implement",
                "capability": "code_modify",
                "goal": "Implement the missing behavior",
                "inputs": {
                    "capability": "code_modify",
                    "instructions": "Implement the missing behavior",
                    "suggested_paths": ["models/selayer.py"],
                },
            }
        ],
    }
    result = materialize(raw)
    assert isinstance(result, WorkflowProposal)
    assert result.tasks[0].inputs.suggested_paths == []


@pytest.mark.parametrize("current", [None, current_workflow()])
def test_materialize_keeps_evidence_descriptions_without_exact_criteria(current) -> None:
    raw = raw_experiment("run")
    raw["tasks"][0]["inputs"].update(
        expected_metrics=["final accuracy reported by the experiment"],
        expected_artifacts=["metrics output file", "error log if execution fails"],
    )
    result = materialize(raw, current=current)
    tasks = result.tasks if isinstance(result, WorkflowProposal) else result.add_tasks
    inputs = tasks[0].inputs
    assert inputs.expected_metrics == []
    assert inputs.expected_artifacts == []
    assert inputs.instructions.startswith("Run run\n")
    assert "final accuracy reported by the experiment" in inputs.instructions
    assert "metrics output file" in inputs.instructions
    assert "error log if execution fails" in inputs.instructions
    assert "not exact metric keys or file paths" in inputs.instructions
    assert "conditional items apply only when their condition holds" in inputs.instructions
    # Sanitizing output does not mutate the input draft.
    assert raw["tasks"][0]["inputs"]["expected_artifacts"] == [
        "metrics output file", "error log if execution fails"
    ]


def test_materialize_does_not_broadcast_evidence_or_guess_exact_looking_names() -> None:
    raw = raw_experiment("run")
    raw["tasks"][0]["inputs"].update(
        expected_metrics=["accuracy"], expected_artifacts=["metrics.json"]
    )
    raw["tasks"].append(raw_experiment("other")["tasks"][0])
    result = materialize(raw)
    assert result.tasks[0].inputs.expected_metrics == []
    assert result.tasks[0].inputs.expected_artifacts == []
    assert "accuracy" in result.tasks[0].inputs.instructions
    assert "metrics.json" in result.tasks[0].inputs.instructions
    assert result.tasks[1].inputs.instructions == "Run other"


def test_deterministic_compiler_preserves_trusted_exact_experiment_criteria() -> None:
    trusted = proposal("task_experiment")
    trusted.tasks[0].inputs = ExperimentRunInput(
        instructions="Measure accuracy and save the result",
        expected_metrics=["accuracy"],
        expected_artifacts=["results/metrics.json"],
    )
    result = DeterministicWorkflowCompiler(trusted).compile(
        work_request(), current=None, registry=registry(), budget=budget()
    ).output
    assert result.tasks[0].inputs.expected_metrics == ["accuracy"]
    assert result.tasks[0].inputs.expected_artifacts == ["results/metrics.json"]


def test_compiler_prompt_places_evidence_intent_in_instructions() -> None:
    llm = _FakeCompilerLLM(raw_experiment())
    LLMWorkflowCompiler(llm).compile(
        work_request(), current=None, registry=registry(), budget=budget()
    )
    assert "put evidence requirements in inputs.instructions" in llm.prompts[0]
    assert "inputs.expected_metrics=[] and inputs.expected_artifacts=[]" in llm.prompts[0]
    assert "error logs only if execution fails" in llm.prompts[0]
    assert "Empty arrays do not waive the need to produce evidence" in llm.prompts[0]


def test_semantic_review_rejects_incomplete_draft_then_recovers() -> None:
    incomplete = raw_experiment("run")
    corrected = {
        "summary": "implement then run",
        "rationale": "implement before the experiment",
        "tasks": [
            {
                "key": "implement",
                "capability": "code_modify",
                "goal": "Implement the missing behavior",
                "inputs": {"capability": "code_modify", "instructions": "Implement it"},
            },
            {
                "key": "run",
                "capability": "experiment_run",
                "goal": "Run the experiment",
                "depends_on": ["implement"],
                "inputs": {"capability": "experiment_run", "instructions": "Run it"},
            },
        ],
    }
    llm = _ScriptedCompilerLLM(
        drafts=[incomplete, corrected],
        reviews=[
            {"accepted": False, "issues": ["implement before the experiment"]},
            {"accepted": True},
        ],
    )
    compiler = LLMWorkflowCompiler(llm)
    result = compiler.compile(
        work_request(), current=None, registry=registry(), budget=budget()
    ).output
    assert isinstance(result, WorkflowProposal)
    assert [task.capability.value for task in result.tasks] == ["code_modify", "experiment_run"]
    assert result.tasks[1].depends_on == [result.tasks[0].id]


def test_semantic_review_accepts_experiment_only_request() -> None:
    llm = _FakeCompilerLLM(raw_experiment("run"), review={"accepted": True})
    compiler = LLMWorkflowCompiler(llm)
    result = compiler.compile(
        work_request(), current=None, registry=registry(), budget=budget()
    ).output
    assert isinstance(result, WorkflowProposal)
    assert [task.capability.value for task in result.tasks] == ["experiment_run"]


def test_review_receives_effective_inputs_constraints_and_shared_capabilities() -> None:
    capabilities = registry()
    capabilities.definitions[0].description = "Modify and verify code, not research metrics."
    capabilities.definitions[1].description = "Measure and deliver experimental evidence."
    raw = raw_repair()
    raw["tasks"][0]["inputs"]["suggested_paths"] = ["guessed.py"]
    raw["tasks"][1]["inputs"].update(
        instructions="Stop on failure; do not modify code.",
        expected_artifacts=["error log only if execution fails"],
    )
    raw["tasks"][1]["constraints"] = ["Use registered data only; do not download."]
    llm = _FakeCompilerLLM(raw)
    result = LLMWorkflowCompiler(llm).compile(
        work_request(), current=None, registry=capabilities, budget=budget(),
    )
    draft_prompt, review_prompt = llm.prompts
    for description in (item.description for item in capabilities.definitions):
        assert description in draft_prompt and description in review_prompt
    for prompt in llm.prompts:
        assert "A small task budget does not expand a capability" in prompt
        assert "Code-level verification does not replace formal experiment delivery" in prompt
    projected_inputs = [
        json.loads(line.removeprefix("  inputs="))
        for line in review_prompt.splitlines() if line.startswith("  inputs=")
    ]
    assert projected_inputs == [t.inputs.model_dump(mode="json") for t in result.output.tasks]
    assert "guessed.py" not in review_prompt
    assert "Stop on failure; do not modify code." in review_prompt
    assert "Use registered data only; do not download." in review_prompt
    assert "depends_on=['fix']" in review_prompt
    assert "need not be repeated in the goal" in review_prompt
    assert "task_fix" not in review_prompt and "work_round1" not in review_prompt
    assert raw["tasks"][0]["inputs"]["suggested_paths"] == ["guessed.py"]


@pytest.mark.parametrize("max_tasks", [1, 2])
def test_scope_review_uses_existing_retry_without_squeezing_experiment_into_coding(max_tasks) -> None:
    """Scripted rejection tests wiring, not a guarantee of model judgment."""
    combined = raw_repair()
    combined["tasks"] = [combined["tasks"][0]]
    combined["tasks"][0]["goal"] = "Change code and deliver formal experiment metrics"
    combined["tasks"][0]["inputs"]["instructions"] = combined["tasks"][0]["goal"]
    llm = _ScriptedCompilerLLM(
        drafts=[combined, raw_repair()],
        reviews=[{"accepted": False, "issues": ["Formal measurements require experiment_run"]}],
    )
    compiler = LLMWorkflowCompiler(llm)
    if max_tasks == 1:
        with pytest.raises(CompilationError, match="2 tasks but only 1 remain") as caught:
            compiler.compile(work_request(), current=None, registry=registry(), budget=budget(1))
        assert caught.value.llm_calls == 3
        assert len(llm.prompts) == 3  # no review of a graph that cannot fit
    else:
        result = compiler.compile(
            work_request(), current=None, registry=registry(), budget=budget(2),
        )
        assert [t.capability for t in result.output.tasks] == [
            Capability.CODE_MODIFY, Capability.EXPERIMENT_RUN,
        ]
        assert result.output.tasks[1].depends_on == [result.output.tasks[0].id]
        assert result.llm_calls == 4
    assert "Formal measurements require experiment_run" in llm.prompts[2]


def test_compilation_review_verdict_and_issues_are_consistent() -> None:
    with pytest.raises(ValidationError, match="accepted review cannot carry issues"):
        CompilationReview(accepted=True, issues=["unexpected"])
    with pytest.raises(ValidationError, match="rejected review requires"):
        CompilationReview(accepted=False)


def test_semantic_review_rejects_twice_then_fails() -> None:
    llm = _ScriptedCompilerLLM(
        drafts=[raw_experiment("run"), raw_experiment("run")],
        reviews=[
            {"accepted": False, "issues": ["implement first"]},
            {"accepted": False, "issues": ["implement first"]},
        ],
    )
    compiler = LLMWorkflowCompiler(llm)
    with pytest.raises(CompilationError, match="rejected after review"):
        compiler.compile(
            work_request(), current=None, registry=registry(), budget=budget()
        )


def test_semantic_review_rejects_speculative_repair_round_then_recovers() -> None:
    speculative = {
        "summary": "run and conditionally repair",
        "rationale": "precompile every possible branch",
        "tasks": [
            *raw_experiment("run")["tasks"],
            {
                "key": "diagnose",
                "capability": "code_modify",
                "goal": "If the run fails, diagnose and fix it",
                "depends_on": ["run"],
                "inputs": {
                    "capability": "code_modify",
                    "instructions": "Diagnose and fix a failure if it occurs",
                },
            },
            {
                "key": "rerun",
                "capability": "experiment_run",
                "goal": "Rerun after a fix",
                "depends_on": ["diagnose"],
                "inputs": {
                    "capability": "experiment_run",
                    "instructions": "Rerun after the fix",
                },
            },
        ],
    }
    llm = _ScriptedCompilerLLM(
        drafts=[speculative, raw_experiment("run")],
        reviews=[
            {
                "accepted": False,
                "issues": [
                    "diagnose and rerun are conditional on a failure not yet observed"
                ],
            },
            {"accepted": True},
        ],
    )
    result = LLMWorkflowCompiler(llm).compile(
        work_request(), current=None, registry=registry(), budget=budget()
    ).output
    assert isinstance(result, WorkflowProposal)
    assert [task.capability for task in result.tasks] == [Capability.EXPERIMENT_RUN]
    assert "ONE currently executable round" in llm.prompts[0]
    assert "conditional on a failure not yet observed" in llm.prompts[2]
    assert "depends_on=['run']" in llm.prompts[1]


def test_materialize_carries_task_constraints() -> None:
    raw = {
        "summary": "implement",
        "rationale": "needed",
        "tasks": [
            {
                "key": "implement",
                "capability": "code_modify",
                "goal": "Implement the missing behavior",
                "constraints": ["Use accuracy as the primary metric"],
                "inputs": {"capability": "code_modify", "instructions": "Implement"},
            }
        ],
    }
    result = materialize(raw)
    assert isinstance(result, WorkflowProposal)
    assert result.tasks[0].constraints == ["Use accuracy as the primary metric"]


def test_scheduler_passes_task_constraints_not_run_constraints() -> None:
    from resagent2_contracts import TaskProposal

    class _CapturePort:
        def __init__(self, result):
            self._result = result
            self.requests = []

        def invoke(self, request):
            self.requests.append(request)
            return self._result

    proposal = WorkflowProposal(
        work_request_id="work_1",
        summary="s",
        compilation_rationale="r",
        tasks=[
            TaskProposal(
                id="task_x",
                work_request_id="work_1",
                capability=Capability.EXPERIMENT_RUN,
                goal="Run",
                constraints=["use accuracy"],
                inputs=ExperimentRunInput(instructions="Run"),
            )
        ],
    )
    port = _CapturePort(ModuleResult(status=ModuleStatus.COMPLETED, summary="done", payload={"env_id": "resenv_test"}))
    scheduler = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT, port=port
            )
        },
        store=InMemoryRunStore(),
    )
    _create_run(scheduler,
        "run_x",
        ResearchRequest(
            goal="g",
            constraints=["stale run-level constraint"],
            budget=RunBudget(
                max_tasks=5, max_attempts_per_task=2, max_llm_calls=20, timeout_seconds=60
            ),
        ),
        proposal,
    )
    scheduler.run_until_stable("run_x")
    assert port.requests[0].constraints == ["use accuracy"]
    assert "stale run-level constraint" not in port.requests[0].constraints


@pytest.mark.parametrize("failure_stage", [CompilationDraft, CompilationReview])
def test_compile_failure_reports_provider_attempts(failure_stage) -> None:
    failure = RuntimeError("provider unavailable")

    class _FailingProvider:
        last_attempts = 0

        def next_action(self, prompt, action_type):
            self.last_attempts = 3 if action_type is failure_stage else 1
            if action_type is failure_stage:
                raise failure
            return raw_experiment()

    compiler = LLMWorkflowCompiler(_FailingProvider())
    with pytest.raises(CompilationError, match="provider unavailable") as caught:
        compiler.compile(
            work_request(), current=None, registry=registry(), budget=budget()
        )
    assert caught.value.llm_calls == (3 if failure_stage is CompilationDraft else 4)
    assert caught.value.__cause__ is failure


def test_compile_usage_is_per_invocation_after_success_and_failure() -> None:
    class _RecoverableProvider(_FakeCompilerLLM):
        fail_next = False

        def next_action(self, prompt, action_type):
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("temporary failure")
            return super().next_action(prompt, action_type)

    client = _RecoverableProvider(raw_experiment())
    compiler = LLMWorkflowCompiler(client)
    first = compiler.compile(
        work_request(), current=None, registry=registry(), budget=budget()
    )
    assert first.llm_calls == 2
    client.fail_next = True
    with pytest.raises(CompilationError, match="temporary failure") as caught:
        compiler.compile(
            work_request(), current=None, registry=registry(), budget=budget()
        )
    assert caught.value.llm_calls == 1
    recovered = compiler.compile(
        work_request(), current=None, registry=registry(), budget=budget()
    )
    assert recovered.llm_calls == 2
    assert first.llm_calls == 2
    assert caught.value.llm_calls == 1


def test_compile_review_prompt_failure_preserves_one_call(monkeypatch) -> None:
    import resagent2_orchestrator.compiler as compiler_module

    failure = RuntimeError("review prompt failed")

    def fail_prompt(request, draft, registry):
        raise failure

    monkeypatch.setattr(compiler_module, "_review_prompt", fail_prompt)
    client = _FakeCompilerLLM(raw_experiment())
    with pytest.raises(CompilationError, match="review prompt failed") as caught:
        LLMWorkflowCompiler(client).compile(
            work_request(), current=None, registry=registry(), budget=budget()
        )
    assert len(client.prompts) == 1
    assert caught.value.llm_calls == 1
    assert caught.value.__cause__ is failure


def test_compile_rejection_preserves_consumption() -> None:
    invalid = {"summary": "invalid", "rationale": "invalid", "tasks": []}
    compiler = LLMWorkflowCompiler(_ScriptedCompilerLLM([invalid, invalid]))
    with pytest.raises(CompilationError, match="2 attempts") as caught:
        compiler.compile(
            work_request(), current=None, registry=registry(), budget=budget()
        )
    assert caught.value.llm_calls == 2
    assert isinstance(caught.value.__cause__, CompilationError)
