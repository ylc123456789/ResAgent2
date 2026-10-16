"""Behavioral acceptance for hierarchy, ordinary input and explicit interruption."""
from datetime import UTC, datetime
import json
from pydantic import BaseModel
import pytest

from e2e.native_fixtures import tool_turns
from resagent2_contracts import (AgentOwner, AgentPermissions, AgentResult, AttemptStatus,
    ModuleStatus, ResearchRequest, RunBudget, RunPermissions, RunStatus, SessionStatus,
    TaskProposal, TaskStatus, WorkflowAgentDefinition, WorkflowAgentKind,
    WorkflowAgentRegistry, WorkflowProposal, WorkRequestStatus)
from resagent2_runtime import (AgentLoop, AgentDefinition, AskUserTool, FinishTool,
    AllowListPermissionPolicy, CompletionDecision, InMemorySessionStore, ScriptedLLMClient)
from resagent2_runtime.cancellation import current_cancellation
from resagent2_runtime.controls import RunControlInbox
from resagent2_components.materials import request_task_context
from resagent2_runtime.models import ContextSection
from resagent2_orchestrator import (ResearchController, WorkflowScheduler, ModuleBinding,
    InMemoryRunStore, DeterministicWorkflowCompiler, DeterministicWorkInterpreter)
from resagent2_scientific import ScientificAgent
from resagent2_scientific.parent_tools import ReplyToTaskInput, ReplyToTaskTool

def action(tool, **arguments):
    return {"tool": tool, "arguments": arguments}

def scientific_finish():
    return action("finish", report="Observed actual retained results and interruption facts",
        artifacts=[{"kind": "scientific_opinion", "path": "opinion.json", "media_type": "application/json",
            "summary": "Conclusion", "content": json.dumps({"verdict": "inconclusive", "statement": "No scientific claim",
            "limitations": ["Interrupted work does not supply complete experimental evidence"]})}])

def work():
    return action("request_work", assessment={"statement": "Need execution"}, work_request={
        "objective": "Implement assigned task", "constraints": ["Preserve original input exactly"],
        "tasks": [{"key": "implement", "instruction": "Do the assigned implementation"}]})

class Check:
    def evaluate(self, state, candidate):
        return CompletionDecision(complete=candidate is not None, report="Task completed")

class MarkInput(BaseModel):
    value: str

class MarkTool:
    """Write one observable marker."""
    name = "mark"
    input_model = MarkInput
    def __init__(self, effects): self.effects = effects
    def execute(self, state, arguments):
        from resagent2_runtime import ToolObservation
        self.effects.append(arguments.value)
        return ToolObservation(summary="Marker written", value=arguments.value)

class Child:
    def __init__(self, client, effects):
        self.requests = []
        self.store = InMemorySessionStore()
        self.loop = AgentLoop(store=self.store)
        tools = (MarkTool(effects), AskUserTool(), FinishTool())
        self.definition = AgentDefinition(name="coding", owner=AgentOwner.CODING,
            system_prompt="Follow upstream requirements", tools=tools, llm_client=client,
            context_builder=lambda request, state, limit: [ContextSection(name="task", content=json.dumps(request_task_context(request))),
                ContextSection(name="artifact_index", content='{"artifacts":[]}')],
            permission_policy=AllowListPermissionPolicy({t.name for t in tools}), completion_check=Check())
    def invoke(self, request):
        self.requests.append(request)
        return self.loop.run(self.definition, request, session_id="session_live_child")
    def terminate_session(self, session_id, reason): self.loop.terminate_session(session_id, reason)

def build(tmp_path, root_client, child):
    scheduler = WorkflowScheduler(bindings={WorkflowAgentKind.CODING: ModuleBinding(owner=AgentOwner.CODING, port=child)},
        store=InMemoryRunStore(), artifact_root=tmp_path / "artifacts", data_root=tmp_path / "data")
    scientific = ScientificAgent(root_client, store=InMemorySessionStore())
    return ResearchController(scientific_port=scientific, scheduler=scheduler,
        registry=WorkflowAgentRegistry(definitions=[WorkflowAgentDefinition(workflow_agent_kind=WorkflowAgentKind.CODING)]),
        compiler=DeterministicWorkflowCompiler(WorkflowProposal(work_request_id="work_fixture", tasks=[TaskProposal(
            id="task_live", work_request_id="work_fixture", workflow_agent_kind=WorkflowAgentKind.CODING,
            instruction="Do the assigned implementation", shared_goal="Implement assigned task",
            shared_constraints=["Preserve original input exactly"])]), patch=None),
        interpreter=DeterministicWorkInterpreter(), control_inbox=RunControlInbox(tmp_path / "controls"))

def request():
    return ResearchRequest(goal="Complete assigned work", permissions=RunPermissions(),
        budget=RunBudget(max_llm_calls=30, timeout_seconds=60))

def test_ordinary_input_finishes_batch_then_parent_clarifies_same_child(tmp_path):
    effects = []
    inbox = RunControlInbox(tmp_path / "controls")
    class ChildClient(ScriptedLLMClient):
        def next_tool_call(self, *args, **kwargs):
            if not self.contexts:
                inbox.submit("run_live", "input", "New user requirement")
            return super().next_tool_call(*args, **kwargs)
    from resagent2_runtime.models import ToolCallTurn
    batch = ToolCallTurn(tool_calls=[{"id": "call_"+value, "name": "mark", "arguments": json.dumps({"value":value})}
                                   for value in ("first", "second")])
    child_client = ChildClient([batch, *tool_turns([action("finish", report="Done")])])
    parent_client = ScriptedLLMClient(tool_turns([work(), action("reply_to_task", task_id="task_live", clarification="Apply the new requirement"), scientific_finish()]))
    child = Child(child_client, effects)
    controller = build(tmp_path, parent_client, child)
    run = controller.create_run("run_live", request())
    assert run.status == RunStatus.COMPLETED, run.terminal_error
    assert effects == ["first", "second"]
    assert len(child.requests) == 2 and child.requests[1].parent_session_id == "session_live_child"
    assert len(run.workflow.tasks[0].attempts) == 1
    assert child.requests[1].upstream_inputs[0].source == "parent"
    assert "New user requirement" in parent_client.contexts[1].task_context
    assert "Apply the new requirement" in child_client.contexts[1].task_context
    assert "shared_constraints" in child_client.contexts[0].task_context
    assert not any(ref.kind in {"question", "answer"} for ref in run.artifacts.values())

def test_explicit_interrupt_invalidates_late_child_reply_and_terminates_work(tmp_path):
    effects = []
    inbox = RunControlInbox(tmp_path / "controls")
    class ChildClient(ScriptedLLMClient):
        def next_tool_call(self, *args, **kwargs):
            inbox.submit("run_stop", "interrupt", "Stop old work and reassess")
            current_cancellation().cancel("Explicit test interruption")
            return super().next_tool_call(*args, **kwargs)
    child_client = ChildClient(tool_turns([action("mark", value="late side effect")]))
    parent_client = ScriptedLLMClient(tool_turns([work(), scientific_finish()]))
    child = Child(child_client, effects)
    controller = build(tmp_path, parent_client, child)
    paused = controller.create_run("run_stop", request())
    assert paused.status == RunStatus.PAUSED and effects == []
    old = paused.work_requests[0]
    assert old.status == WorkRequestStatus.CANCELLED and old.outcome.status == WorkRequestStatus.CANCELLED
    task = paused.workflow.tasks[0]
    assert task.status == TaskStatus.CANCELLED and task.attempts[0].status == AttemptStatus.CANCELLED
    assert child.store.load("session_live_child").status == SessionStatus.CANCELLED
    assert paused.runtime_notices[-1]["stop_confirmed"]
    resumed = controller.resume_run("run_stop")
    assert resumed.status == RunStatus.COMPLETED, resumed.terminal_error
    assert len(child.requests) == 1
    assert resumed.scientific_session.id == paused.scientific_session.id
    assert "Stop old work and reassess" in parent_client.contexts[1].task_context
    assert any(turn.runtime_notice and "work_interrupted" in turn.runtime_notice for turn in controller.scientific_port.store.load(resumed.scientific_session.id).tool_turns)

def test_parent_can_read_interrupted_command_through_same_task_artifact_handoff(tmp_path):
    from dataclasses import replace
    from resagent2_components import RegisteredArtifactReader, preserve_execution_record
    from resagent2_orchestrator.handoffs import read_json
    from resagent2_runtime.cancellation import OperationCancelled

    facts = {"operation": "process", "command": "python train.py", "pid": 321,
             "exit_code": -15, "stdout_path": "partial.stdout", "stderr_path": "partial.stderr",
             "duration_seconds": 1.25, "files_retained": True}
    partial = tmp_path / "partial.checkpoint"

    class InterruptedCommand:
        name = "run_shell"
        input_model = MarkInput
        def execute(self, state, arguments):
            partial.write_bytes(b"actual incomplete checkpoint")
            controller.control_inbox.submit("run_stop_record", "interrupt", "Stop and inspect what happened")
            current_cancellation().cancel("User interrupted command")
            raise OperationCancelled("User interrupted command", details=facts)

    class CommandChild(Child):
        def __init__(self, client):
            super().__init__(client, [])
            self.definition = replace(self.definition, tools=(InterruptedCommand(), FinishTool()),
                permission_policy=AllowListPermissionPolicy({"run_shell", "finish"}))
        def invoke(self, request):
            return preserve_execution_record(super().invoke(request), self.store)

    child = CommandChild(ScriptedLLMClient(tool_turns([action("run_shell", value="run command")])))
    parent_client = ScriptedLLMClient(tool_turns([work(), scientific_finish()]))
    controller = build(tmp_path, parent_client, child)
    paused = controller.create_run("run_stop_record", request())
    assert paused.status == RunStatus.PAUSED
    task = paused.workflow.tasks[0]
    attempt = task.attempts[0]
    assert task.status == TaskStatus.CANCELLED
    record = next(paused.artifacts[key] for key in attempt.artifact_ids
                  if paused.artifacts[key].kind == "execution_record")
    assert read_json(record) == {"results": [{**facts, "execution_status": "interrupted", "stop_confirmed": True}]}
    report = paused.artifacts[attempt.report_artifact_id]
    assert record.id in report.metadata["output_artifact_ids"]
    assert record.id in paused.scientific_artifact_ids
    notice = next(item for item in paused.runtime_notices if item["type"] == "work_interrupted")
    assert notice["stop_confirmed"] is True and record.id in notice["artifact_ids"]
    assert "not validated deliveries" in notice["file_retention_note"]
    assert not any(item.kind == "code_change" for item in paused.artifacts.values())
    assert partial.read_bytes() == b"actual incomplete checkpoint"
    child_state = child.store.load("session_live_child")
    assert len(child_state.tool_turns[0].tool_results) == 1

    # The parent uses only public refs; details do not enter its requirements.
    resumed = controller.resume_run(paused.run_id)
    assert resumed.status == RunStatus.COMPLETED, resumed.terminal_error
    context = parent_client.contexts[1]
    assert record.id in context.artifact_index
    assert facts["command"] not in context.task_context
    directory = json.loads(context.artifact_index.split("\n", 1)[1])
    indexed_task = directory["work_requests"][0]["tasks"][0]
    assert indexed_task["task_id"] == task.id
    assert any(item["artifact_id"] == record.id for item in indexed_task["artifacts"])
    reader = RegisteredArtifactReader(list(resumed.artifacts.values()), run_id=resumed.run_id)
    assert json.loads(reader.read_text(record.id)["content"])["results"][0]["command"] == "python train.py"
    assert len(child.requests) == 1
    assert sum(item.kind == "execution_record" for item in resumed.artifacts.values()) == 1
    assert partial.read_bytes() == b"actual incomplete checkpoint"


def test_user_forward_keeps_original_identity_time_and_parent_cannot_invent_source(tmp_path):
    from resagent2_contracts import AgentRequest, BusinessInput, TaskBudget
    event = BusinessInput(event_id="original", text="Exact original words", source="user", created_at=datetime(2020,1,1,tzinfo=UTC))
    req = AgentRequest(run_id="run_forward", agent=AgentOwner.SCIENTIFIC, instruction="Do work", budget=TaskBudget(max_llm_calls=10,timeout_seconds=60),
        permissions=AgentPermissions(request_work=True), upstream_inputs=[event], task_context={"child_tasks": [{"task_id": "task_live"}]})
    tool = ReplyToTaskTool(req)
    state = type("State", (), {"session_id": "session_parent"})()
    with pytest.raises(PermissionError, match="reference"):
        tool.execute(state, ReplyToTaskInput(task_id="task_live", user_input_event_id="invented"))
    assert tool.execute(state, ReplyToTaskInput(task_id="task_live", user_input_event_id="original")).task_reply["user_input_event_id"] == "original"


def test_interrupt_while_waiting_keeps_prior_command_record_readable_to_parent(tmp_path):
    from dataclasses import replace
    from e2e.native_fixtures import ParentQuestionClient
    from resagent2_components import preserve_execution_record
    from resagent2_runtime import ToolObservation
    from resagent2_orchestrator.handoffs import read_json
    from resagent2_contracts import VerificationResult

    facts = dict(command="python prepare.py", exit_code=0, timed_out=False,
        stdout_path="prepared.stdout", stderr_path="prepared.stderr", duration_seconds=0.1)
    class Command:
        name = "run_shell"
        input_model = MarkInput
        def execute(self, state, arguments):
            return ToolObservation(summary="Preparation completed", value=facts)
    class CommandChild(Child):
        def __init__(self, client):
            super().__init__(client, [])
            self.definition = replace(self.definition, tools=(Command(), AskUserTool(), FinishTool()),
                permission_policy=AllowListPermissionPolicy({"run_shell", "ask_user", "finish"}))
        def invoke(self, request):
            return preserve_execution_record(super().invoke(request), self.store)

    parent = ParentQuestionClient(tool_turns([work(), scientific_finish()]))
    child = CommandChild(ScriptedLLMClient(tool_turns([
        action("run_shell", value="prepare"),
        action("ask_user", text="Choose how to continue", requested_fields=["choice"]),
    ])))
    controller = build(tmp_path, parent, child)
    waiting = controller.create_run("run_wait_record", request())
    assert waiting.pending_question and waiting.workflow.tasks[0].status == TaskStatus.NEEDS_USER_INPUT
    prior = next(ref for ref in waiting.artifacts.values() if ref.kind == "execution_record")
    assert read_json(prior) == {"results": [VerificationResult(**facts).model_dump(mode="json")]}
    controller.control_inbox.submit(waiting.run_id, "interrupt", "Stop while awaiting a decision")
    paused = controller.resume_run(waiting.run_id)
    assert paused.status == RunStatus.PAUSED and paused.pending_question is None
    assert prior.id in paused.scientific_artifact_ids
    assert paused.artifacts[prior.id] == prior
    assert child.store.load("session_live_child").status == SessionStatus.CANCELLED
    resumed = controller.resume_run(paused.run_id)
    assert resumed.status == RunStatus.COMPLETED, resumed.terminal_error
    assert prior.id in parent.contexts[-1].artifact_index
    assert len(child.requests) == 1


def test_input_between_last_agent_return_and_owner_receiving_is_not_swallowed(tmp_path):
    client = ScriptedLLMClient(tool_turns([scientific_finish(), scientific_finish()]))
    child = Child(ScriptedLLMClient([]), [])
    controller = build(tmp_path, client, child)
    original = controller.scientific_port.invoke
    def invoke(request):
        result = original(request)
        if len(client.contexts) == 1:
            controller.control_inbox.submit("run_final_input", "input", "Clarify the final conclusion")
        return result
    controller.scientific_port.invoke = invoke
    run = controller.create_run("run_final_input", request())
    assert run.status == RunStatus.COMPLETED, run.terminal_error
    assert len(client.contexts) == 2
    assert "Clarify the final conclusion" in client.contexts[1].task_context
    assert sum(ref.kind == "scientific_opinion" for ref in run.artifacts.values()) == 1
    assert len(run.consumed_control_ids) == 1


def test_escalation_reference_forwards_real_user_answer_and_child_history_stays_part_three(tmp_path):
    from e2e.native_fixtures import ParentQuestionClient, tool_turn
    from resagent2_contracts import UserAnswer
    class Parent(ParentQuestionClient):
        def next_tool_call(self, context, schemas, turns, *, max_input_tokens):
            questions = json.loads(context.pending_notifications or "[]")
            if questions and questions[0]["id"] in self._asked_child_ids:
                data = json.loads(context.task_context.split("\n",1)[1])
                user = [event["record"] for event in data["business_dialogue"] if event["type"] == "answer"][-1]
                self.contexts.append(context)
                self.schemas.append(schemas)
                self.histories.append([t.model_copy(deep=True) for t in turns])
                return tool_turn("reply_to_task", {"task_id": "task_live", "question_id": questions[0]["id"],
                                                  "user_answer_question_id": user["question_id"]})
            return super().next_tool_call(context, schemas, turns, max_input_tokens=max_input_tokens)
    child_client = ScriptedLLMClient(tool_turns([action("ask_user", text="Choose a method", requested_fields=["method"]), action("finish", report="Done")]))
    parent_client = Parent(tool_turns([work(), scientific_finish()]))
    child = Child(child_client, [])
    controller = build(tmp_path, parent_client, child)
    paused = controller.create_run("run_escalate", request())
    child_question = next(iter(paused.child_questions.values()))
    assert paused.pending_question.related_question_id == child_question.id
    assert child_question.text not in [e["record"] for e in json.loads(parent_client.contexts[1].task_context.split("\n",1)[1])["business_dialogue"] if e["type"] == "answer"]
    run = controller.answer_question(paused.run_id, UserAnswer(question_id=paused.pending_question.id,
        values={"method": "custom free text, exactly"}, answered_at=datetime.now(UTC)))
    assert run.status == RunStatus.COMPLETED, run.terminal_error
    child_answer = child.requests[1].user_answers[0]
    assert child_answer.source == "user" and child_answer.original_question_id == paused.pending_question.id
    assert child_answer.values == {"method": "custom free text, exactly"}
    history = controller.scientific_port.store.load(run.scientific_session.id).tool_turns
    assert any(t.runtime_notice and '"type": "child_question"' in t.runtime_notice for t in history)
    assert not run.child_questions


def test_interrupt_waiting_escalation_cancels_only_related_upper_question(tmp_path):
    from e2e.native_fixtures import ParentQuestionClient
    parent = ParentQuestionClient(tool_turns([work(), scientific_finish()]))
    child = Child(ScriptedLLMClient(tool_turns([action("ask_user", text="Need user choice", requested_fields=["choice"])])), [])
    controller = build(tmp_path, parent, child)
    waiting = controller.create_run("run_escalated_stop", request())
    assert waiting.pending_question.related_question_id
    controller.control_inbox.submit(waiting.run_id, "interrupt")
    stopped = controller.resume_run(waiting.run_id)
    assert stopped.status == RunStatus.PAUSED and stopped.pending_question is None and not stopped.child_questions
    assert stopped.work_requests[0].status == WorkRequestStatus.CANCELLED
    assert controller.scientific_port.store.load(stopped.scientific_session.id).pending_control is None
    assert stopped.user_wait_seconds >= 0
    resumed = controller.resume_run(stopped.run_id)
    assert resumed.status == RunStatus.COMPLETED, resumed.terminal_error


def test_parent_clarifies_all_affected_tasks_before_releasing_child_work(tmp_path):
    from dataclasses import replace

    effects = []
    inbox = RunControlInbox(tmp_path / "controls")
    class FirstClient(ScriptedLLMClient):
        def next_tool_call(self, *args, **kwargs):
            if not self.contexts:
                inbox.submit("run_multi_input", "input", "New requirement applies to both Tasks")
            return super().next_tool_call(*args, **kwargs)
    first = FirstClient(tool_turns([action("mark", value="initial"), action("finish", report="First done")]))
    second = ScriptedLLMClient(tool_turns([action("finish", report="Second done")]))
    class Children(Child):
        def invoke(self, request):
            self.requests.append(request)
            definition = replace(self.definition, llm_client=first if request.task_id == "task_first" else second)
            return self.loop.run(definition, request, session_id="session_" + request.task_id)
    child = Children(first, effects)
    work_call = action("request_work", assessment={"statement": "Need both Tasks"}, work_request={
        "objective": "Implement assigned task", "constraints": ["Preserve original input exactly"],
        "tasks": [{"key": "first", "instruction": "Do first assigned implementation"},
                  {"key": "second", "instruction": "Do second assigned implementation"}]})
    class ParentClient(ScriptedLLMClient):
        def next_tool_call(self, *args, **kwargs):
            if len(self.contexts) == 2:
                waiting = controller.scheduler.store.load("run_multi_input")
                assert waiting.parent_attention
                assert waiting.workflow.tasks[0].status == TaskStatus.NEEDS_USER_INPUT
                assert waiting.workflow.tasks[1].status == TaskStatus.PENDING
                assert [r.task_id for r in child.requests] == ["task_first"]
                assert waiting.task_inputs["task_first"][0].text == "Apply requirement to first Task"
                assert not waiting.child_questions
            return super().next_tool_call(*args, **kwargs)
    parent_client = ParentClient(tool_turns([work_call,
        action("reply_to_task", task_id="task_first", clarification="Apply requirement to first Task", resume_work=False),
        action("reply_to_task", task_id="task_second", clarification="Apply requirement to second Task", resume_work=True),
        scientific_finish()]))
    controller = build(tmp_path, parent_client, child)
    controller.compiler = DeterministicWorkflowCompiler(WorkflowProposal(work_request_id="work_fixture", tasks=[
        TaskProposal(id="task_" + key, work_request_id="work_fixture", workflow_agent_kind=WorkflowAgentKind.CODING,
            instruction="Do " + key + " assigned implementation", shared_goal="Implement assigned task",
            shared_constraints=["Preserve original input exactly"]) for key in ("first", "second")]), patch=None)
    run = controller.create_run("run_multi_input", request())
    assert run.status == RunStatus.COMPLETED, run.terminal_error
    assert [r.task_id for r in child.requests] == ["task_first", "task_first", "task_second"]
    assert child.requests[1].parent_session_id == "session_task_first"
    assert [len(t.attempts) for t in run.workflow.tasks] == [1, 1]
    assert "Apply requirement to first Task" in first.contexts[1].task_context
    assert "Apply requirement to second Task" in second.contexts[0].task_context
    assert len(parent_client.contexts) == 4
    assert effects == ["initial"]


def test_parent_can_hold_answered_child_and_cannot_release_unanswered_question(tmp_path):
    from e2e.native_fixtures import tool_turn
    class ParentClient(ScriptedLLMClient):
        def next_tool_call(self, context, schemas, turns, *, max_input_tokens):
            index = len(self.contexts)
            if index in {1, 2, 3}:
                waiting = controller.scheduler.store.load("run_hold_answer")
                assert waiting.parent_attention
                assert waiting.workflow.tasks[0].status == TaskStatus.NEEDS_USER_INPUT
                assert len(child.requests) == 1
                self.contexts.append(context)
                self.schemas.append(schemas)
                self.histories.append([t.model_copy(deep=True) for t in turns])
                if index == 1:
                    assert waiting.child_questions
                    return tool_turn("reply_to_task", {})
                if index == 2:
                    question = next(iter(waiting.child_questions.values()))
                    return tool_turn("reply_to_task", {"task_id": "task_live", "question_id": question.id,
                        "values": {"choice": "Parent decision"}, "resume_work": False})
                assert not waiting.child_questions and waiting.answers[-1].source == "parent"
                return tool_turn("reply_to_task", {"resume_work": True})
            return super().next_tool_call(context, schemas, turns, max_input_tokens=max_input_tokens)
    parent = ParentClient(tool_turns([work(), scientific_finish()]))
    child_client = ScriptedLLMClient(tool_turns([action("ask_user", text="Need parent choice", requested_fields=["choice"]),
                                              action("finish", report="Done")]))
    child = Child(child_client, [])
    controller = build(tmp_path, parent, child)
    run = controller.create_run("run_hold_answer", request())
    assert run.status == RunStatus.COMPLETED, run.terminal_error
    assert len(child.requests) == 2 and child.requests[1].parent_session_id == "session_live_child"
    assert len(run.workflow.tasks[0].attempts) == 1
    assert child.requests[1].user_answers[0].values == {"choice": "Parent decision"}
    assert not run.child_questions


@pytest.mark.parametrize("arrival_stage", ["validation", "rendering"])
def test_input_during_completion_receiving_reconsiders_frozen_opinion(tmp_path, arrival_stage):
    first = scientific_finish()
    second = scientific_finish()
    changed = json.loads(second["arguments"]["artifacts"][0]["content"])
    changed["statement"] = "Conclusion revised for the new upstream requirement"
    second["arguments"]["artifacts"][0]["content"] = json.dumps(changed)
    client = ScriptedLLMClient(tool_turns([first, second]))
    controller = build(tmp_path, client, Child(ScriptedLLMClient([]), []))
    gate, renderer = controller.gate, controller.report_renderer
    submitted = False
    def publish_once():
        nonlocal submitted
        if not submitted:
            controller.control_inbox.submit("run_receiving_input", "input", "Revise the final conclusion")
            submitted = True
    class Gate:
        def validate(self, run, result, refs):
            if arrival_stage == "validation":
                publish_once()
            return gate.validate(run, result, refs)
    class Renderer:
        def render(self, data):
            if arrival_stage == "rendering":
                publish_once()
            return renderer.render(data)
    controller.gate, controller.report_renderer = Gate(), Renderer()
    run = controller.create_run("run_receiving_input", request())
    assert run.status == RunStatus.COMPLETED, run.terminal_error
    assert len(client.contexts) == 2
    assert "Revise the final conclusion" in client.contexts[1].task_context
    assert run.final_opinion.statement == changed["statement"]
    opinions = [ref for ref in run.artifacts.values() if ref.kind == "scientific_opinion"]
    assert len(opinions) == 2 and len({ref.sha256 for ref in opinions}) == 2
    # The already frozen first proposal remains readable for reconsideration;
    # the second proposal alone supplies the accepted final conclusion.
    assert opinions[0].id in client.contexts[1].artifact_index
    assert sum(ref.kind == "final_report" for ref in run.artifacts.values()) == 1
    assert not controller.control_inbox.pending(run.run_id, run.consumed_control_ids)
    assert not run.pending_registrations


def test_input_publication_cannot_cross_terminal_commit_using_stale_ui_snapshot(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from resagent2_cli.main import _submit_control

    controller = build(tmp_path, ScriptedLLMClient(tool_turns([scientific_finish()])),
                       Child(ScriptedLLMClient([]), []))
    final_registration = controller.scheduler.artifact_registry.register_final_report
    registering, allow_finish, submitting = Event(), Event(), Event()
    def register(*args, **kwargs):
        registering.set()
        assert allow_finish.wait(5)
        return final_registration(*args, **kwargs)
    controller.scheduler.artifact_registry.register_final_report = register
    def publish(stale):
        submitting.set()
        return _submit_control(tmp_path, stale, kind="input", text="Too late for this Run",
                               store=controller.scheduler.store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(controller.create_run, "run_commit_fence", request())
        assert registering.wait(5)
        stale = controller.scheduler.store.load("run_commit_fence")
        assert stale.status == RunStatus.RUNNING
        producer = pool.submit(publish, stale)
        try:
            assert submitting.wait(2)
            assert not producer.done()
        finally:
            allow_finish.set()
        completed = owner.result(timeout=5)
        with pytest.raises(ValueError, match="running or paused"):
            producer.result(timeout=5)
    assert completed.status == RunStatus.COMPLETED
    assert not controller.control_inbox.pending(completed.run_id)
