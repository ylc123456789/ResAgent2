"""Contract tests for the Phase 7 WorkflowCompiler (DEVELOPMENT_PLAN §7.2)."""

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from resagent2_contracts import (
    AgentOwner,
    Capability,
    CapabilityDefinition,
    CapabilityRegistry,
    CodeModifyInput,
    ErrorCode,
    ExperimentRunInput,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    ResearchRequest,
    RunBudget,
    TaskProposal,
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

NOW = datetime(2026, 8, 28, tzinfo=UTC)


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


def budget() -> RunBudget:
    return RunBudget(
        max_tasks=5,
        max_attempts_per_task=2,
        max_llm_calls=20,
        timeout_seconds=60,
    )


def proposal(*task_ids: str) -> WorkflowProposal:
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


class _FakeCompilerLLM:
    """Returns a fixed raw dict, recording the prompt and target schema."""

    def __init__(self, raw: dict) -> None:
        self._raw = raw
        self.prompts: list[str] = []
        self.schemas: list[type[BaseModel]] = []

    def next_action(self, prompt: str, action_type: type[BaseModel]) -> dict:
        self.prompts.append(prompt)
        self.schemas.append(action_type)
        return self._raw


def test_deterministic_compiler_returns_proposal_for_new_graph() -> None:
    compiler = DeterministicWorkflowCompiler(proposal("task_experiment"))
    result = compiler.compile(
        work_request(), current=None, registry=registry(), budget=budget()
    )
    assert isinstance(result, WorkflowProposal)
    assert result.work_request_id == "work_round1"


def test_deterministic_compiler_returns_patch_for_existing_graph() -> None:
    current = Workflow(
        run_id="run_example",
        revision=1,
        tasks=[WorkflowTask(
            id="task_experiment",
            work_request_id="work_round1",
            capability=Capability.EXPERIMENT_RUN,
            goal="Run",
            inputs=ExperimentRunInput(instructions="Run"),
        )],
        created_from="work_round1",
    )
    patch = WorkflowPatch(
        work_request_id="work_round1",
        based_on_revision=1,
        reason="add repair",
    )
    compiler = DeterministicWorkflowCompiler(proposal("task_experiment"), patch)
    result = compiler.compile(
        work_request(), current=current, registry=registry(), budget=budget()
    )
    assert isinstance(result, WorkflowPatch)
    assert result.based_on_revision == 1


def test_deterministic_compiler_requires_patch_for_existing_graph() -> None:
    current = Workflow(
        run_id="run_example",
        revision=1,
        tasks=[],
        created_from="work_round1",
    )
    compiler = DeterministicWorkflowCompiler(proposal("task_experiment"))
    with pytest.raises(CompilationError, match="no patch"):
        compiler.compile(
            work_request(), current=current, registry=registry(), budget=budget()
        )


def test_llm_compiler_validates_and_binds_work_request_id() -> None:
    # LLM returns a WRONG work_request_id; the compiler must force the real one.
    raw = {
        "work_request_id": "work_wrong",
        "summary": "compiled",
        "compilation_rationale": "semantic",
        "tasks": [
            {
                "id": "task_experiment",
                "work_request_id": "work_wrong",
                "capability": "experiment_run",
                "goal": "Run",
                "rationale": "evidence",
                "inputs": {"capability": "experiment_run", "instructions": "Run"},
            }
        ],
    }
    compiler = LLMWorkflowCompiler(_FakeCompilerLLM(raw))
    result = compiler.compile(
        work_request(), current=None, registry=registry(), budget=budget()
    )
    assert isinstance(result, WorkflowProposal)
    assert result.work_request_id == "work_round1"
    assert result.tasks[0].work_request_id == "work_round1"


def test_llm_compiler_rejects_invalid_json() -> None:
    compiler = LLMWorkflowCompiler(_FakeCompilerLLM({"summary": "no work_request_id"}))
    with pytest.raises(CompilationError, match="invalid WorkflowProposal"):
        compiler.compile(
            work_request(), current=None, registry=registry(), budget=budget()
        )


def test_llm_compiler_rejects_undeclared_capability() -> None:
    # The LLM invents code_understand, which the registry does not declare.
    raw = {
        "work_request_id": "work_round1",
        "summary": "compiled",
        "compilation_rationale": "semantic",
        "tasks": [
            {
                "id": "task_understand",
                "work_request_id": "work_round1",
                "capability": "code_understand",
                "goal": "Inspect",
                "rationale": "evidence",
                "inputs": {"capability": "code_understand", "question": "Where?"},
            }
        ],
    }
    compiler = LLMWorkflowCompiler(_FakeCompilerLLM(raw))
    with pytest.raises(CompilationError, match="undeclared"):
        compiler.compile(
            work_request(), current=None, registry=registry(), budget=budget()
        )


def test_llm_compiler_rejects_undeclared_workspace_id() -> None:
    # The LLM invents a workspace id that the composition root did not declare.
    raw = {
        "work_request_id": "work_round1",
        "summary": "compiled",
        "compilation_rationale": "semantic",
        "tasks": [
            {
                "id": "task_experiment",
                "work_request_id": "work_round1",
                "capability": "experiment_run",
                "goal": "Run",
                "rationale": "evidence",
                "workspace_id": "ws_evil",
                "inputs": {"capability": "experiment_run", "instructions": "Run"},
            }
        ],
    }
    compiler = LLMWorkflowCompiler(_FakeCompilerLLM(raw))
    with pytest.raises(CompilationError, match="workspace"):
        compiler.compile(
            work_request(),
            current=None,
            registry=registry(),
            budget=budget(),
            workspaces=[
                WorkspaceDescriptor(
                    workspace_id="ws_main", source_kind=WorkspaceSourceKind.LOCAL
                )
            ],
        )


def test_llm_compiler_rejects_cyclic_graph() -> None:
    raw = {
        "work_request_id": "work_round1",
        "summary": "cycle",
        "compilation_rationale": "semantic",
        "tasks": [
            {
                "id": "task_a",
                "work_request_id": "work_round1",
                "capability": "experiment_run",
                "goal": "A",
                "rationale": "x",
                "depends_on": ["task_b"],
                "inputs": {"capability": "experiment_run", "instructions": "A"},
            },
            {
                "id": "task_b",
                "work_request_id": "work_round1",
                "capability": "experiment_run",
                "goal": "B",
                "rationale": "x",
                "depends_on": ["task_a"],
                "inputs": {"capability": "experiment_run", "instructions": "B"},
            },
        ],
    }
    compiler = LLMWorkflowCompiler(_FakeCompilerLLM(raw))
    with pytest.raises(CompilationError, match="cycle"):
        compiler.compile(
            work_request(), current=None, registry=registry(), budget=budget()
        )


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
