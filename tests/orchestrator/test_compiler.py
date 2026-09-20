"""Compiler structure, correction budget, routing and immutable graph materialization."""
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from resagent2_contracts import (
    RunBudget,
    WorkRequest,
    WorkRequestDraft,
    Workflow,
    WorkflowAgentDefinition,
    WorkflowAgentRegistry,
    WorkflowPatch,
    WorkflowTask,
    WorkspaceDescriptor,
)
from resagent2_orchestrator import CompilationError, DeterministicWorkflowCompiler, LLMWorkflowCompiler
from resagent2_orchestrator.compiler import CompilationDraft, _materialize_draft


def work_request():
    now = datetime.now(UTC)
    return WorkRequest(id="work_test", run_id="run_test", scientific_session_id="session_test",
        request=WorkRequestDraft(objective="Measure the method", expected_evidence=["A measured result"]),
        created_at=now, updated_at=now)


def registry():
    return WorkflowAgentRegistry(definitions=[WorkflowAgentDefinition(workflow_agent_kind=k)
        for k in ["coding", "experiment"]])


def budget(max_tasks=5):
    return RunBudget(max_tasks=max_tasks, max_attempts_per_task=2, max_llm_calls=20, timeout_seconds=60)


def raw_task(key="measure", **changes):
    return dict(key=key, workflow_agent_kind="experiment", instruction="Measure the method") | changes


def raw(*tasks):
    return {"tasks": list(tasks) or [raw_task()]}


def current_workflow():
    return Workflow(run_id="run_test", revision=3, created_from="work_previous", tasks=[WorkflowTask(
        id="task_measure", work_request_id="work_previous", workflow_agent_kind="experiment", instruction="Old work")])


def materialize(data, **changes):
    args = dict(request=work_request(), current=None, registry=registry(), budget=budget(), workspaces=[])
    args.update(changes)
    return _materialize_draft(CompilationDraft.model_validate(data), **args)


class Client:
    def __init__(self, *replies, attempts=1):
        self.replies = iter(replies)
        self.last_attempts = attempts
        self.prompts = []
        self.schemas = []
        self.limits = []

    def next_action(self, prompt, action_type):
        self.prompts.append(prompt)
        self.schemas.append(action_type)
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def set_attempt_limit(self, remaining):
        self.limits.append(remaining)


def compile_with(client, **changes):
    args = dict(current=None, registry=registry(), budget=budget()) | changes
    return LLMWorkflowCompiler(client).compile(work_request(), **args)


def test_deterministic_compiler_preserves_trusted_submission():
    proposal = materialize(raw())
    compiler = DeterministicWorkflowCompiler(proposal)
    assert compiler.compile(work_request(), current=None, registry=registry(), budget=budget()).output == proposal
    with pytest.raises(CompilationError, match="no patch"):
        compiler.compile(work_request(), current=current_workflow(), registry=registry(), budget=budget())
    patch = materialize(raw(), current=current_workflow())
    assert DeterministicWorkflowCompiler(proposal, patch).compile(work_request(), current=current_workflow(),
        registry=registry(), budget=budget()).output == patch


@pytest.mark.parametrize("field", ["summary", "rationale", "reason", "unknown"])
def test_draft_rejects_parallel_graph_prose(field):
    with pytest.raises(ValidationError):
        CompilationDraft.model_validate(raw() | {field: "extra"})


@pytest.mark.parametrize("field,value", [("capability", "code_modify"), ("goal", "measure"),
    ("inputs", {}), ("constraints", []), ("acceptance_spec", {}), ("permissions", {})])
def test_task_draft_rejects_parallel_task_content_and_invented_control(field, value):
    with pytest.raises(ValidationError):
        CompilationDraft.model_validate(raw(raw_task(**{field: value})))


def test_graph_rejects_scientific_before_any_execution():
    with pytest.raises(ValidationError, match="coding.*experiment"):
        CompilationDraft.model_validate(raw(raw_task(workflow_agent_kind="scientific")))


@pytest.mark.parametrize("tasks,match", [
    ([], "at least 1"), ([raw_task(), raw_task()], "duplicate"),
    ([raw_task(depends_on=["missing"])], "unknown"),
    ([raw_task("a", depends_on=["b"]), raw_task("b", depends_on=["a"])], "cycle"),
])
def test_invalid_graph_shapes_are_rejected(tasks, match):
    with pytest.raises(ValueError, match=match):
        materialize({"tasks": tasks})


def test_materialization_checks_registry_budget_and_workspace():
    with pytest.raises(CompilationError, match="undeclared Agent"):
        materialize(raw(), registry=WorkflowAgentRegistry(definitions=[]))
    with pytest.raises(CompilationError, match="task budget"):
        materialize(raw(raw_task("a"), raw_task("b")), budget=budget(1))
    with pytest.raises(CompilationError, match="undeclared workspace"):
        materialize(raw(raw_task(workspace_id="ws_missing")))
    workspaces = [WorkspaceDescriptor(workspace_id="ws_a", source_kind="local")]
    assert materialize(raw(), workspaces=workspaces).tasks[0].workspace_id == "ws_a"
    workspaces.append(WorkspaceDescriptor(workspace_id="ws_b", source_kind="local"))
    with pytest.raises(CompilationError, match="multiple workspaces"):
        materialize(raw(), workspaces=workspaces)


def test_materialization_assigns_ids_and_append_only_revision():
    proposal = materialize(raw(raw_task("a"), raw_task("b", depends_on=["a"])))
    assert [t.id for t in proposal.tasks] == ["task_a", "task_b"]
    assert proposal.tasks[1].depends_on == ["task_a"]
    current = current_workflow()
    before = current.model_dump()
    patch = materialize(raw(), current=current)
    assert isinstance(patch, WorkflowPatch)
    assert patch.based_on_revision == 3
    assert patch.add_tasks[0].id != current.tasks[0].id
    assert current.model_dump() == before


def test_future_binding_preserves_names_and_direct_dependencies():
    producer = raw_task("producer", output_names=["metrics"])
    consumer = raw_task("consumer", depends_on=["producer"],
        input_artifact_bindings=[dict(source_task="producer", output_selector="metrics")])
    proposal = materialize(raw(producer, consumer))
    assert proposal.tasks[1].input_artifact_bindings[0].source_task == "task_producer"
    assert proposal.tasks[0].output_names == ["metrics"]
    for changed in [consumer | {"depends_on": []}, consumer | {"input_artifact_bindings": [dict(source_task="producer", output_selector="unknown")]}]:
        with pytest.raises(ValueError):
            materialize(raw(producer, changed))


def test_valid_compile_uses_one_model_call_and_no_semantic_review():
    client = Client(raw())
    result = compile_with(client)
    assert result.llm_calls == 1
    assert client.schemas == [CompilationDraft]
    assert result.output.tasks[0].instruction == "Measure the method"
    assert "Do not invent" in client.prompts[0]
    assert "Scientific is never a graph node" in client.prompts[0]


@pytest.mark.parametrize("bad", [{"tasks": []}, raw(raw_task(depends_on=["missing"])),
    json.JSONDecodeError("invalid", "bad", 0)])
def test_invalid_structure_or_json_gets_one_bounded_correction(bad):
    client = Client(bad, raw())
    result = compile_with(client, remaining_calls=5)
    assert result.llm_calls == 2
    assert client.limits == [5, 4]
    assert "Previous draft rejected" in client.prompts[1]


def test_invalid_draft_fails_after_two_attempts_and_keeps_metering():
    with pytest.raises(CompilationError) as raised:
        compile_with(Client(raw(raw_task(depends_on=["missing"])), raw(raw_task(depends_on=["missing"]))))
    assert raised.value.llm_calls == 2


def test_provider_retry_attempts_count_against_compiler_budget():
    with pytest.raises(CompilationError) as raised:
        compile_with(Client(raw(), attempts=3), remaining_calls=2)
    assert raised.value.llm_calls == 3
    with pytest.raises(CompilationError) as raised:
        compile_with(Client(RuntimeError("provider down"), attempts=3))
    assert raised.value.llm_calls == 3


def test_no_remaining_task_or_call_budget_invokes_no_model():
    for changes in [dict(remaining_calls=0), dict(current=current_workflow(), budget=budget(1))]:
        client = Client(raw())
        with pytest.raises(CompilationError) as raised:
            compile_with(client, **changes)
        assert raised.value.llm_calls == 0
        assert client.schemas == []


def test_typed_draft_copies_are_revalidated():
    draft = CompilationDraft.model_validate(raw()).model_copy(update={"tasks": []})
    assert compile_with(Client(draft, raw())).llm_calls == 2


def test_compile_usage_resets_between_calls():
    client = Client(raw(), RuntimeError("down"), raw())
    compiler = LLMWorkflowCompiler(client)
    args = dict(current=None, registry=registry(), budget=budget())
    assert compiler.compile(work_request(), **args).llm_calls == 1
    with pytest.raises(CompilationError) as raised:
        compiler.compile(work_request(), **args)
    assert raised.value.llm_calls == 1
    assert compiler.compile(work_request(), **args).llm_calls == 1
