"""Pure task routing, source preservation and bounded native output."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from resagent2_contracts import (
    ExecutionLimits, WorkRequest, WorkRequestDraft, Workflow, WorkflowAgentDefinition,
    WorkflowAgentRegistry, WorkflowPatch, WorkflowTask, WorkspaceDescriptor,
)
from resagent2_orchestrator import CompilationError, DeterministicWorkflowCompiler, LLMWorkflowCompiler
from resagent2_orchestrator.compiler import RouteTasks, _materialize_routes
from resagent2_runtime import ScriptedLLMClient, PromptLLMClient
from resagent2_runtime.budget import BudgetExhaustedError, execution_budget, current_budget
from e2e.native_fixtures import tool_turn


def original_task(key="measure", **changes):
    return dict(key=key, instruction="Measure the method") | changes


def work_request(*tasks, **changes):
    now = datetime.now(UTC)
    return WorkRequest(id="work_test", run_id="run_test", scientific_session_id="session_test",
        request=WorkRequestDraft(objective="Assess the method", constraints=["Keep the requested scope"],
            tasks=list(tasks) or [original_task()], **changes), created_at=now, updated_at=now)


def registry():
    return WorkflowAgentRegistry(definitions=[WorkflowAgentDefinition(workflow_agent_kind=k)
        for k in ["coding", "experiment"]])


def budget(max_tasks=5):
    return ExecutionLimits(max_tasks=max_tasks, max_attempts_per_task=2)


def route(key="measure", **changes):
    return dict(task_key=key, workflow_agent_kind="experiment") | changes


def raw(*routes):
    return {"routes": list(routes) or [route()]}


def current_workflow():
    return Workflow(run_id="run_test", revision=3, created_from="work_previous", tasks=[WorkflowTask(
        id="task_measure", work_request_id="work_previous", workflow_agent_kind="experiment", instruction="Old work")])


def materialize(data=None, **changes):
    args = dict(request=work_request(), current=None, registry=registry(), limits=budget(), workspaces=[])
    args.update(changes)
    return _materialize_routes(RouteTasks.model_validate(data or raw()), **args)


class Client:
    manages_usage = True
    def __init__(self, *replies, attempts=1):
        self.replies = iter(replies)
        self.last_attempts = attempts
        self.prompts, self.schemas, self.limits = [], [], []

    def next_action(self, prompt, action_type):
        call_id = uuid4().hex
        for index in range(self.last_attempts):
            scope = current_budget()
            scope.charge(call_id, index)
            scope.usage.complete(call_id, index, "succeeded")
        self.prompts.append(prompt)
        self.schemas.append(action_type)
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def set_attempt_limit(self, remaining):
        self.limits.append(remaining)


def compile_with(client, *, max_llm_calls=50, request=None, **changes):
    args = dict(current=None, registry=registry(), limits=budget()) | changes
    with execution_budget(max_llm_calls=max_llm_calls, timeout_seconds=60):
        return LLMWorkflowCompiler(client).compile(request or work_request(), **args)


def test_deterministic_compiler_preserves_trusted_submission():
    proposal = materialize()
    compiler = DeterministicWorkflowCompiler(proposal)
    assert compiler.compile(work_request(), current=None, registry=registry(), limits=budget()).output == proposal
    with pytest.raises(CompilationError, match="no patch"):
        compiler.compile(work_request(), current=current_workflow(), registry=registry(), limits=budget())


@pytest.mark.parametrize("field,value", [("instruction", "rewritten"), ("depends_on", []),
    ("workspace_id", "ws_other"), ("input_artifacts", []), ("output_names", []), ("permissions", {})])
def test_router_schema_cannot_change_task_fields(field, value):
    with pytest.raises(ValidationError):
        RouteTasks.model_validate(raw(route(**{field: value})))


@pytest.mark.parametrize("routes", [[route(), route()], [route("unknown")], [route(), route("extra")]])
def test_routing_cannot_duplicate_delete_or_add_tasks(routes):
    with pytest.raises(CompilationError):
        materialize({"routes": routes})


def test_unrouteable_judgment_returns_to_scientific_without_retry():
    client = Client({"routes": [{"task_key": "measure", "reason": "Combines incompatible responsibilities"}]})
    with pytest.raises(CompilationError, match="incompatible") as error:
        compile_with(client)
    assert error.value.llm_calls == len(client.schemas) == 1


@pytest.mark.parametrize("task,match", [
    (original_task(depends_on=["missing"]), "unknown"),
    (original_task(depends_on=["measure"]), "cycle"),
])
def test_original_invalid_graph_rejected_before_routing(task, match):
    with pytest.raises(ValidationError, match=match):
        work_request(task)


def test_resources_and_capacity_checked_before_model():
    for request, changes, match in [
        (work_request(), {"registry": WorkflowAgentRegistry(definitions=[])}, "undeclared Agent"),
        (work_request(original_task("a"), original_task("b")), {"limits": budget(1)}, "task limits"),
        (work_request(original_task(workspace_id="ws_missing")), {}, "undeclared workspace"),
    ]:
        with pytest.raises(CompilationError, match=match):
            materialize(raw(*[route(t.key) for t in request.request.tasks]), request=request, **changes)
    workspaces = [WorkspaceDescriptor(workspace_id="ws_a", source_kind="local")]
    assert materialize(workspaces=workspaces).tasks[0].workspace_id == "ws_a"
    workspaces.append(WorkspaceDescriptor(workspace_id="ws_b", source_kind="local"))
    client = Client(raw())
    with pytest.raises(CompilationError, match="multiple workspaces"):
        compile_with(client, workspaces=workspaces)
    assert client.schemas == []


def test_instruction_controls_and_shared_requirements_are_copied_exactly():
    request = work_request(
        original_task("producer", instruction="Implement exactly A, then explain limitations", output_names=["metrics"]),
        original_task("consumer", instruction="Measure only the requested outcome", depends_on=["producer"],
            input_artifact_ids=["artifact_given"],
            input_artifact_bindings=[dict(source_task="producer", output_selector="metrics")]),
    )
    before = request.model_dump()
    proposal = materialize(raw(route("consumer"), route("producer", workflow_agent_kind="coding")), request=request)
    assert request.model_dump() == before
    for original, task in zip(request.request.tasks, proposal.tasks):
        assert task.instruction == original.instruction
        assert task.shared_goal == request.request.objective
        assert task.shared_constraints == request.request.constraints
        assert task.input_artifacts == original.input_artifact_ids
        assert task.output_names == original.output_names
    assert [t.id for t in proposal.tasks] == ["task_producer", "task_consumer"]
    assert proposal.tasks[1].depends_on == ["task_producer"]
    assert proposal.tasks[1].input_artifact_bindings[0].source_task == "task_producer"


def test_new_round_assigns_fresh_ids_and_preserves_existing_workflow():
    current = current_workflow()
    before = current.model_dump()
    patch = materialize(current=current)
    assert isinstance(patch, WorkflowPatch)
    assert patch.based_on_revision == 3
    assert patch.add_tasks[0].id != current.tasks[0].id
    assert current.model_dump() == before


def test_valid_compile_one_call_no_semantic_review():
    client = Client(raw())
    result = compile_with(client)
    assert result.llm_calls == 1
    assert client.schemas == [RouteTasks]
    assert result.output.tasks[0].instruction == "Measure the method"
    assert "not a task planner" in client.prompts[0]


@pytest.mark.parametrize("bad", [{"routes": []}, raw(route("missing")), json.JSONDecodeError("invalid", "bad", 0)])
def test_invalid_structure_has_one_bounded_correction(bad):
    client = Client(bad, raw())
    result = compile_with(client, max_llm_calls=5)
    assert result.llm_calls == 2
    assert client.limits == [5, 4]
    assert "Previous routing rejected" in client.prompts[1]


def test_invalid_routing_fails_after_two_attempts():
    with pytest.raises(CompilationError) as raised:
        compile_with(Client(raw(route("missing")), raw(route("missing"))))
    assert raised.value.llm_calls == 2


def test_provider_attempts_count_against_unique_budget():
    with pytest.raises(BudgetExhaustedError):
        compile_with(Client(raw(), attempts=3), max_llm_calls=2)
    with pytest.raises(CompilationError) as raised:
        compile_with(Client(RuntimeError("provider down"), attempts=3))
    assert raised.value.llm_calls == 3


def test_no_budget_or_task_capacity_invokes_no_model():
    client = Client(raw())
    with pytest.raises(BudgetExhaustedError):
        compile_with(client, max_llm_calls=0)
    assert client.schemas == []
    with pytest.raises(CompilationError):
        compile_with(client, current=current_workflow(), limits=budget(1))
    assert client.schemas == []


def test_invalid_final_budgeted_output_cannot_correct():
    client = Client({"routes": []}, raw())
    with pytest.raises(BudgetExhaustedError):
        compile_with(client, max_llm_calls=1)
    assert len(client.schemas) == 1


def test_typed_output_copies_are_revalidated():
    result = RouteTasks.model_validate(raw()).model_copy(update={"routes": []})
    assert compile_with(Client(result, raw())).llm_calls == 2


def test_native_adapter_has_only_route_tasks_schema_and_no_history():
    client = ScriptedLLMClient([tool_turn("route_tasks", raw())])
    adapter = PromptLLMClient(client, system_prompt="Route tasks", section_name="compiler_request", max_context_tokens=4096, output_tool_name="route_tasks")
    with execution_budget(max_llm_calls=3, timeout_seconds=60):
        assert adapter.next_action("Match the original task", RouteTasks).routes[0].task_key == "measure"
    assert [item["function"]["name"] for item in client.schemas[0]] == ["route_tasks"]
    assert client.histories == [[]]
    assert client.contexts[0].included_sections == ["system", "compiler_request"]


@pytest.mark.parametrize("turn", [tool_turn("finish", {}), tool_turn("route_tasks", raw(), call_id="first").model_copy(update={"tool_calls": []})])
def test_native_adapter_rejects_other_or_missing_output(turn):
    adapter = PromptLLMClient(ScriptedLLMClient([turn]), system_prompt="Route tasks", max_context_tokens=4096, output_tool_name="route_tasks")
    with execution_budget(max_llm_calls=3, timeout_seconds=60), pytest.raises(ValueError, match="exactly one"):
        adapter.next_action("Match", RouteTasks)


def test_cancelled_routing_does_not_retry_or_wrap_interruption():
    from resagent2_runtime.cancellation import OperationCancelled
    client = Client(OperationCancelled("User interrupted routing"), raw())
    with pytest.raises(OperationCancelled):
        compile_with(client)
    assert len(client.schemas) == 1



def test_original_requirement_whitespace_survives_routing_and_dispatch(tmp_path):
    from resagent2_contracts import AgentOwner, AgentResult, ModuleStatus, ResearchRequest, RunBudget, RunPermissions, RunStatus
    from resagent2_orchestrator import InMemoryRunStore, ModuleBinding, ResearchRun, ScriptedModulePort, WorkflowScheduler

    instruction = "\n  Implement the requested method.\nDo not measure held-out data.  \n"
    objective = "\n  Assess the method within the requested boundary.\n"
    constraint = "  Preserve the original split and freeze before measurement.\n"
    request = work_request(original_task(instruction=instruction))
    request.request = WorkRequestDraft(objective=objective, constraints=[constraint], tasks=request.request.tasks)
    request = WorkRequest.model_validate_json(request.model_dump_json())
    proposal = materialize(request=request)
    port = ScriptedModulePort([AgentResult(status=ModuleStatus.COMPLETED, report="Done")])
    engine = WorkflowScheduler(bindings={"experiment": ModuleBinding(owner=AgentOwner.EXPERIMENT, port=port)},
        store=InMemoryRunStore(), artifact_root=tmp_path / "artifacts", data_root=tmp_path / "data")
    now = datetime.now(UTC)
    engine.store.save(ResearchRun(run_id=request.run_id,
        request=ResearchRequest(goal="Assess the method", budget=RunBudget(max_llm_calls=5, timeout_seconds=60), permissions=RunPermissions()),
        status=RunStatus.RUNNING, created_at=now, updated_at=now))
    accepted = engine.accept_proposal(request.run_id, proposal)
    stored_task = accepted.workflow.tasks[0]
    assert stored_task.instruction == instruction
    assert stored_task.shared_goal == objective
    assert stored_task.shared_constraints == [constraint]
    result = engine.execute_task(request.run_id, stored_task.id)
    assert result.workflow.tasks[0].status == "completed"
    delivered = port.requests[0]
    assert delivered.instruction == instruction
    assert delivered.task_context["shared_goal"] == objective
    assert delivered.task_context["shared_constraints"] == [constraint]


@pytest.mark.parametrize("field", ["instruction", "objective", "constraint"])
def test_original_requirement_fields_reject_whitespace_only(field):
    values = dict(objective="Goal", constraints=["Boundary"], tasks=[original_task()])
    if field == "instruction":
        values["tasks"][0]["instruction"] = " \n\t"
    elif field == "objective":
        values["objective"] = " \n\t"
    else:
        values["constraints"] = [" \n\t"]
    with pytest.raises(ValidationError, match="non-whitespace"):
        WorkRequestDraft(**values)
