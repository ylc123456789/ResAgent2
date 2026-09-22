"""Native Agent approval resumes must re-audit and actually run the approved command."""

from datetime import UTC, datetime
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from resagent2_coding import NativeCodingAgent
from resagent2_experiment import NativeExperimentAgent
from resagent2_components import PreparedEnvironment, ResourceLayout
from resagent2_contracts import (
    AgentOwner, AgentPermissions, AgentRequest, ArtifactCandidate, QuestionDraft,
    RecordedAnswer, TaskBudget, WorkspaceAccess, WorkspaceGrant,
)
from resagent2_orchestrator.artifacts import ArtifactRegistry
from resagent2_runtime import JsonSessionStore, ScriptedLLMClient
from resagent2_runtime.budget import execution_budget


FINISH = {"tool": "finish", "arguments": {"report": "Recorded the actual outcome"}}


@pytest.fixture(params=["coding", "experiment"])
def case(tmp_path, monkeypatch, request):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("*.marker\n__pycache__/\n")
    (repo / "measure.py").write_text(
        "from pathlib import Path\nimport sys\n"
        "with Path(sys.argv[1] + '.marker').open('a') as f:\n"
        "    f.write('executed\\n')\n"
    )
    (repo / "test_markers.py").write_text(
        "from pathlib import Path\nimport unittest\n"
        "class Markers(unittest.TestCase):\n"
        "    def test_first(self):\n"
        "        with Path('first.marker').open('a') as f: f.write('executed\\n')\n"
        "    def test_second(self):\n"
        "        with Path('second.marker').open('a') as f: f.write('executed\\n')\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "-qm", "baseline"], cwd=repo, check=True,
    )

    # Only environment discovery/audit are fake. ProcessRunner executes real Python.
    conda = tmp_path / "conda"
    conda.write_text(
        f"#!{sys.executable}\nimport os, sys\n"
        "args = sys.argv[1:]\n"
        "command = args[args.index('-p') + 2:]\n"
        "os.execv(sys.executable, [sys.executable, *command[1:]])\n"
    )
    conda.chmod(0o755)
    manager = SimpleNamespace(
        conda_exe=str(conda), audits=[], success=True, after_audit=lambda: None,
    )
    environment = PreparedEnvironment(
        env_id="resenv_test", prefix=Path(sys.prefix),
        python_version=".".join(map(str, sys.version_info[:3])),
    )
    manager.inspect = lambda **kwargs: environment

    def audit(current):
        assert current == environment
        manager.audits.append(current)
        manager.after_audit()
        return {
            "success": manager.success, "prefix_match": manager.success,
            "pip_available": True, "python_version": current.python_version,
            "sys_prefix": str(current.prefix),
        }

    manager.audit = audit
    kind = request.param
    monkeypatch.setattr(
        f"resagent2_{kind}.agent.EnvironmentManager", lambda **kwargs: manager,
    )
    owner = AgentOwner.CODING if kind == "coding" else AgentOwner.EXPERIMENT
    req = AgentRequest(
        run_id="run_approval", task_id="task_approval", attempt_number=1,
        agent=owner, instruction="Execute first then second, with separate approval",
        budget=TaskBudget(max_llm_calls=30, timeout_seconds=60),
        permissions=AgentPermissions(execute_commands=True, prepare_environment=False),
        confirm_commands=True,
        workspace=WorkspaceGrant(
            root=str(repo), source="local",
            access=WorkspaceAccess(read_paths=["."], write_paths=["."]),
        ),
        workspace_id="ws_main", output_dir=str(tmp_path / "output"),
    )
    return SimpleNamespace(
        root=tmp_path, repo=repo, manager=manager, request=req, kind=kind,
        agent_type=NativeCodingAgent if kind == "coding" else NativeExperimentAgent,
    )


def command(case, tag):
    if case.kind == "coding":
        return {"tool": "run_verification", "arguments": {
            "commands": [f"python -m unittest test_markers.Markers.test_{tag}"],
        }}
    return {"tool": "run_command", "arguments": {"command": f"python measure.py {tag}"}}


def invoke(case, req, actions):
    # Recreate both Agent and SessionStore on every invoke, as controller resume does.
    agent = case.agent_type(
        ScriptedLLMClient(actions), store=JsonSessionStore(case.root / "sessions"),
        resource_layout=ResourceLayout(resource_root=case.root / "resources"),
    )
    return agent.invoke(req)


def approve(case, req, result):
    artifact = next(item for item in result.artifacts if item.kind == "question")
    question = QuestionDraft.model_validate_json(artifact.content)
    answer = RecordedAnswer(
        question_id=f"question_{question.action.action_id}", question_text=question.text,
        requested_fields=question.requested_fields, options=question.options,
        values={"approve": "yes"}, answered_at=datetime.now(UTC),
        run_id=req.run_id, task_id=req.task_id, attempt_number=req.attempt_number,
        action=question.action,
    )
    ref = ArtifactRegistry(case.root / "artifacts").register_system_artifact(
        ArtifactCandidate(
            kind="answer", path="answer.json", media_type="application/json",
            summary="Approve the exact pending command", content=answer.model_dump_json(),
        ),
        run_id=req.run_id, source_type="controller_answer",
        task_id=req.task_id, attempt_number=req.attempt_number,
    )
    return req.model_copy(update={
        "parent_session_id": result.session.id,
        "input_artifacts": [ref], "resume_artifact_ids": [ref.id],
    })


@pytest.mark.parametrize("second_tag", ["first", "second"])
def test_native_commands_resume_with_fresh_audit_and_single_use_approval(case, second_tag):
    first = invoke(case, case.request, [command(case, "first")])
    assert first.status == "needs_user_input"
    assert case.manager.audits == []
    assert list(case.repo.glob("*.marker")) == []

    resumed = approve(case, case.request, first)
    second = invoke(case, resumed, [command(case, "first"), command(case, second_tag)])
    assert second.status == "needs_user_input"
    assert len(case.manager.audits) == 1
    assert (case.repo / "first.marker").read_text() == "executed\n"
    assert not (case.repo / "second.marker").exists()
    first_action = QuestionDraft.model_validate_json(first.artifacts[0].content).action
    pending = JsonSessionStore(case.root / "sessions").load(second.session.id).pending_action
    assert pending.action_id != first_action.action_id

    final = invoke(
        case, approve(case, resumed, second), [command(case, second_tag), FINISH],
    )
    assert final.status == "completed"
    assert len(case.manager.audits) == 2
    expected = "executed\n" * (2 if second_tag == "first" else 1)
    assert (case.repo / "first.marker").read_text() == expected
    if second_tag == "second":
        assert (case.repo / "second.marker").read_text() == "executed\n"
    state = JsonSessionStore(case.root / "sessions").load(final.session.id)
    assert state.pending_action is None
    assert state.memory["env_audit"]["success"]
    executed = [
        event.data for event in state.events
        if event.type == "observation"
        and event.tool in {"run_command", "run_verification"}
        and isinstance(event.data.get("value"), dict)
        and "env_audit" in event.data["value"]
    ]
    assert len(executed) == 2
    assert all(event["value"]["env_audit"]["success"] for event in executed)


def test_failed_audit_after_approval_blocks_actual_command(case):
    first = invoke(case, case.request, [command(case, "first")])
    case.manager.success = False
    result = invoke(case, approve(case, case.request, first), [command(case, "first"), FINISH])
    assert len(case.manager.audits) == 1
    assert not (case.repo / "first.marker").exists()
    state = JsonSessionStore(case.root / "sessions").load(result.session.id)
    assert state.memory["env_audit"]["success"] is False
    assert any(
        event.type == "observation"
        and (event.data.get("value") or {}).get("reason") == "environment_audit_failed"
        for event in state.events
    )


def test_deadline_expiring_during_audit_prevents_command(case):
    first = invoke(case, case.request, [command(case, "first")])
    now = [0.0]
    case.manager.after_audit = lambda: now.__setitem__(0, 61.0)
    with execution_budget(max_llm_calls=30, timeout_seconds=60, clock=lambda: now[0]):
        result = invoke(case, approve(case, case.request, first), [command(case, "first"), FINISH])
    assert result.status == "failed"
    assert result.error.code == "timeout"
    assert len(case.manager.audits) == 1
    assert not (case.repo / "first.marker").exists()


@pytest.mark.parametrize("tool", ["command", "audit"])
def test_process_permission_denies_audit_and_command_before_effects(case, tool):
    req = case.request.model_copy(update={"permissions": AgentPermissions(
        execute_commands=False, prepare_environment=False,
    )})
    action = command(case, "first") if tool == "command" else {"tool": "audit_env", "arguments": {}}
    result = invoke(case, req, [action, FINISH])
    assert result.status == "completed"
    assert case.manager.audits == []
    assert not (case.repo / "first.marker").exists()

def test_stale_first_answer_cannot_change_second_command_or_session(case):
    from resagent2_contracts import (
        PendingQuestion, ResearchRequest, RunBudget, RunPermissions, UserAnswer,
        WorkflowAgentRegistry,
    )
    from resagent2_orchestrator import (
        JsonRunStore, OrchestrationError, ResearchController, ResearchRun,
    )

    first = invoke(case, case.request, [command(case, "first")])
    resumed = approve(case, case.request, first)
    second = invoke(case, resumed, [command(case, "first"), command(case, "second")])
    assert second.status == "needs_user_input"
    old_question = QuestionDraft.model_validate_json(first.artifacts[0].content)
    draft = QuestionDraft.model_validate_json(second.artifacts[0].content)
    assert draft.action.arguments == command(case, "second")["arguments"]
    now = datetime.now(UTC)
    pending = PendingQuestion(
        id="question_second", run_id=case.request.run_id,
        task_id=case.request.task_id, attempt_number=1, created_at=now,
        **draft.model_dump(exclude={"schema_version"}),
    )
    run_store = JsonRunStore(case.root / "runs")
    run_store.save(ResearchRun(
        run_id=case.request.run_id, status="paused", pending_question=pending,
        request=ResearchRequest(
            goal=case.request.instruction,
            budget=RunBudget(max_llm_calls=30, timeout_seconds=60),
            permissions=RunPermissions(execute_commands=True, prepare_environment=False),
        ),
        created_at=now, updated_at=now,
    ))

    def forbidden(*args, **kwargs):
        raise AssertionError("Stale answers must never resume execution")

    controller = ResearchController(
        scientific_port=SimpleNamespace(invoke=forbidden),
        compiler=SimpleNamespace(compile=forbidden),
        scheduler=SimpleNamespace(store=run_store, resume_task_in_place=forbidden),
        registry=WorkflowAgentRegistry(definitions=[]),
    )
    files = [*run_store.root.glob("*.json"), *(case.root / "sessions").glob("*.json")]
    before = {path: path.read_bytes() for path in files}
    with pytest.raises(OrchestrationError, match="answer does not match pending question"):
        controller.answer_question(
            case.request.run_id,
            UserAnswer(
                question_id=f"question_{old_question.action.action_id}",
                values={"approve": "yes"}, answered_at=now,
            ),
        )
    assert {path: path.read_bytes() for path in files} == before
    assert len(case.manager.audits) == 1
    assert (case.repo / "first.marker").read_text() == "executed\n"
    assert not (case.repo / "second.marker").exists()

