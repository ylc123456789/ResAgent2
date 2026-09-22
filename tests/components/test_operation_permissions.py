"""Shared operation decisions and durable single-use approval."""
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from resagent2_capabilities import DeletePathTool
from resagent2_components.operations import command_decision
from resagent2_components.permissions import OperationPermissionPolicy
from resagent2_components.workspace import WorkspaceBoundary
from resagent2_contracts import (
    AgentOwner, AgentPermissions, AgentRequest, ArtifactCandidate, QuestionDraft,
    RecordedAnswer, TaskBudget, WorkspaceAccess, WorkspaceGrant,
)
from resagent2_orchestrator.artifacts import ArtifactRegistry
from resagent2_runtime import (
    AgentDefinition, AgentLoop, CompletionDecision, FinishTool, JsonSessionStore,
    ScriptedLLMClient, ToolObservation,
)


class CommandInput(BaseModel):
    command: str


class Command:
    name = "run_command"
    input_model = CommandInput

    def __init__(self):
        self.calls = []
        self.before_execute = lambda: None

    def execute(self, state, arguments):
        self.before_execute()
        self.calls.append(arguments.command)
        return ToolObservation(summary="Executed", value={"command": arguments.command})


class Completion:
    def evaluate(self, state, candidate):
        return CompletionDecision(complete=candidate is not None, report="Done")


def request(root, **changes):
    fields = dict(
        run_id="run_permissions", task_id="task_permissions", attempt_number=1,
        agent=AgentOwner.CODING, instruction="Do the authorized work",
        budget=TaskBudget(max_llm_calls=8, timeout_seconds=60),
        permissions=AgentPermissions(execute_commands=True, prepare_environment=True),
        confirm_commands=True,
        workspace=WorkspaceGrant(root=str(root), source="local",
                                 access=WorkspaceAccess(read_paths=["."], write_paths=["."])),
    )
    return AgentRequest(**(fields | changes))


def execute(req, actions, tool, store):
    boundary = WorkspaceBoundary(req.workspace)
    tools = (tool, FinishTool())
    definition = AgentDefinition(
        name="permissions", owner=AgentOwner.CODING, system_prompt="Test",
        tools=tools, llm_client=ScriptedLLMClient(actions),
        context_builder=lambda *_: [],
        permission_policy=OperationPermissionPolicy(
            tools, boundary=boundary,
            binding=SimpleNamespace(current=None, hard_constraint=None), request=req,
        ),
        completion_check=Completion(),
    )
    return AgentLoop(store=store).run(definition, req, session_id="session_permissions")


def answer(tmp_path, req, result):
    question = QuestionDraft.model_validate_json(result.artifacts[0].content)
    recorded = RecordedAnswer(
        question_id="question_permissions", question_text=question.text,
        requested_fields=question.requested_fields, options=question.options,
        values={"approve": "yes"}, answered_at=datetime.now(UTC),
        run_id=req.run_id, task_id=req.task_id, attempt_number=req.attempt_number,
        action=question.action,
    )
    ref = ArtifactRegistry(tmp_path / "artifacts").register_system_artifact(
        ArtifactCandidate(kind="answer", path="answer.json", media_type="application/json",
                          summary="Approval", content=recorded.model_dump_json()),
        run_id=req.run_id, source_type="controller_answer",
        task_id=req.task_id, attempt_number=req.attempt_number,
    )
    return req.model_copy(update={
        "parent_session_id": result.session.id,
        "input_artifacts": [ref], "resume_artifact_ids": [ref.id],
    })


def command(text):
    return {"tool": "run_command", "arguments": {"command": text}}


FINISH = {"tool": "finish", "arguments": {"report": "Done"}}


@pytest.mark.parametrize("second", ["python first.py", "python second.py"])
def test_approval_is_consumed_once_and_never_authorizes_next_command(tmp_path, second):
    for name in ("first.py", "second.py"):
        (tmp_path / name).write_text("pass")
    req = request(tmp_path)
    tool = Command()
    store = JsonSessionStore(tmp_path / "sessions")
    first = execute(req, [command("python first.py")], tool, store)
    assert first.status == "needs_user_input" and tool.calls == []
    first_action = store.load(first.session.id).pending_action
    resumed = answer(tmp_path, req, first)
    next_result = execute(resumed, [command("python first.py"), command(second)], tool, store)
    assert next_result.status == "needs_user_input"
    assert tool.calls == ["python first.py"]
    assert store.load(first.session.id).pending_action.action_id != first_action.action_id
    final_req = answer(tmp_path, resumed, next_result)
    final = execute(final_req, [command(second), FINISH], tool, store)
    assert final.status == "completed"
    assert tool.calls == ["python first.py", second]
    assert store.load(first.session.id).pending_action is None


@pytest.mark.parametrize("restriction", ["operation", "paths"])
def test_approval_cannot_override_current_run_authorization(tmp_path, restriction):
    (tmp_path / "first.py").write_text("pass")
    req, tool = request(tmp_path), Command()
    store = JsonSessionStore(tmp_path / "sessions")
    first = execute(req, [command("python first.py")], tool, store)
    resumed = answer(tmp_path, req, first)
    if restriction == "operation":
        resumed.permissions.execute_commands = False
    else:
        resumed.workspace.access = WorkspaceAccess(read_paths=["."], write_paths=["results"])
    result = execute(resumed, [command("python first.py"), FINISH], tool, store)
    assert result.status == "completed" and tool.calls == []
    events = store.load(first.session.id).events
    assert any(event.type == "observation" and not event.data["ok"] for event in events)


def test_recursive_delete_requires_new_approval_if_target_changes(tmp_path):
    target = tmp_path / "cache"
    target.mkdir()
    (target / "old.txt").write_text("old")
    req = request(tmp_path, confirm_commands=False)
    store = JsonSessionStore(tmp_path / "sessions")
    tool = DeletePathTool(WorkspaceBoundary(req.workspace))
    action = {"tool": "delete_path", "arguments": {"path": "cache", "recursive": True}}
    first = execute(req, [action], tool, store)
    resumed = answer(tmp_path, req, first)
    (target / "new.txt").write_text("new")
    second = execute(resumed, [action], tool, store)
    assert second.status == "needs_user_input"
    assert sorted(path.name for path in target.iterdir()) == ["new.txt", "old.txt"]
    final = execute(answer(tmp_path, resumed, second), [action, FINISH], tool, store)
    assert final.status == "completed" and not target.exists()
    assert store.load(first.session.id).memory["edit_revision"] == 1


def test_consumed_approval_is_durable_before_effect_and_not_replayed_after_crash(tmp_path):
    class Interrupted(BaseException):
        pass
    (tmp_path / "first.py").write_text("pass")
    req, tool = request(tmp_path), Command()
    store = JsonSessionStore(tmp_path / "sessions")
    first = execute(req, [command("python first.py")], tool, store)
    resumed = answer(tmp_path, req, first)
    attempts = []
    def interrupt():
        assert store.load(first.session.id).pending_action is None
        attempts.append("started")
        raise Interrupted()
    tool.before_execute = interrupt
    with pytest.raises(Interrupted):
        execute(resumed, [command("python first.py")], tool, store)
    result = execute(resumed, [FINISH], tool, JsonSessionStore(store.root))
    assert result.status == "completed" and attempts == ["started"]


@pytest.mark.parametrize("text,outcome", [
    ("rm data.txt", "deny"), ("rmdir cache", "deny"), ("sudo python a.py", "deny"),
    ("bash -c 'echo hi'", "deny"), ("git reset --hard", "deny"),
    ("git status", "allow"), ("python -c 'print(1)'", "ask"),
    ("python -u first.py", "allow"), ("python -m pytest tests", "allow"),
    ("custom-tool first.py", "ask"),
])
def test_fixed_rules_use_executable_and_argv(tmp_path, text, outcome):
    (tmp_path / "first.py").write_text("pass")
    assert command_decision(text, WorkspaceBoundary(request(tmp_path).workspace)).outcome == outcome
