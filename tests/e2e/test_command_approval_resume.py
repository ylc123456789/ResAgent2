"""Native Agent approval resumes must re-audit and actually run the approved command."""

from e2e.native_fixtures import tool_turns

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
    AgentOwner, AgentPermissions, AgentRequest,
    RecordedAnswer, TaskBudget, WorkspaceAccess, WorkspaceGrant,
)
from resagent2_orchestrator.artifacts import ArtifactRegistry
from resagent2_runtime import JsonSessionStore, ScriptedLLMClient
from resagent2_runtime.budget import execution_budget


FINISH = {"tool": "finish", "arguments": {"report": "Recorded the actual outcome"}}


@pytest.fixture(params=["coding", "coding_shell", "experiment"])
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
        "os.environ['PATH'] = os.path.dirname(sys.executable) + os.pathsep + os.environ['PATH']\n"
        "os.execvp(command[0], command)\n"
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
    kind = request.param.split("_")[0]
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
        shell=request.param != "coding",
        agent_type=NativeCodingAgent if kind == "coding" else NativeExperimentAgent,
    )


def command(case, tag):
    if not case.shell:
        return {"tool": "run_verification", "arguments": {
            "commands": [f"python -m unittest test_markers.Markers.test_{tag}"],
        }}
    return {"tool": "run_shell", "arguments": {"command": f"python measure.py {tag}"}}


def invoke(case, req, actions):
    # Recreate both Agent and SessionStore on every invoke, as controller resume does.
    agent = case.agent_type(
        ScriptedLLMClient(tool_turns(actions)), store=JsonSessionStore(case.root / "sessions"),
        resource_layout=ResourceLayout(resource_root=case.root / "resources"),
    )
    return agent.invoke(req)


def approve(case, req, result):
    question = result.control.question
    answer = RecordedAnswer(
        question_id=question.id, question_text=question.text,
        requested_fields=question.requested_fields, options=question.options,
        values={"approve": "yes"}, answered_at=datetime.now(UTC),
        run_id=req.run_id, session_id=result.session.id, task_id=req.task_id, attempt_number=req.attempt_number,
        action=question.action,
    )
    return req.model_copy(update={
        "parent_session_id": result.session.id,
        "resume_answers": [answer], "control_ack": result.control.origin,
    })



@pytest.mark.parametrize("second_tag", ["first", "second"])
def test_native_commands_resume_with_fresh_audit_and_single_use_approval(case, second_tag):
    first = invoke(case, case.request, [command(case, "first")])
    assert first.status == "needs_user_input"
    assert case.manager.audits == []
    assert list(case.repo.glob("*.marker")) == []

    resumed = approve(case, case.request, first)
    second = invoke(case, resumed, [command(case, second_tag)])
    assert second.status == "needs_user_input"
    assert len(case.manager.audits) == 1
    assert (case.repo / "first.marker").read_text() == "executed\n"
    assert not (case.repo / "second.marker").exists()
    first_action = first.control.question.action
    pending = JsonSessionStore(case.root / "sessions").load(second.session.id).pending_action
    assert pending.action_id != first_action.action_id

    final = invoke(
        case, approve(case, resumed, second), [FINISH],
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
        and event.tool in {"run_shell", "run_verification"}
        and isinstance(event.data.get("value"), dict)
        and "env_audit" in event.data["value"]
    ]
    assert len(executed) == 2
    assert all(event["value"]["env_audit"]["success"] for event in executed)


@pytest.mark.parametrize("case", ["experiment"], indirect=True)
@pytest.mark.parametrize("mode", ["validate", "all"])
def test_pipeline_module_runs_once_after_approval(case, mode):
    package = case.repo / "pipeline"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "run.py").write_text(
        "from pathlib import Path\nimport sys\n"
        "with Path(sys.argv[1] + '.marker').open('a') as f:\n"
        "    f.write('executed\\n')\n"
    )
    action = {"tool": "run_shell", "arguments": {"command": f"python -m pipeline.run {mode}"}}

    first = invoke(case, case.request, [action])
    assert first.status == "needs_user_input"
    assert case.manager.audits == []
    assert not (case.repo / f"{mode}.marker").exists()

    result = invoke(case, approve(case, case.request, first), [FINISH])
    assert result.status == "completed", result.report
    assert len(case.manager.audits) == 1
    assert (case.repo / f"{mode}.marker").read_text() == "executed\n"
    state = JsonSessionStore(case.root / "sessions").load(result.session.id)
    assert state.pending_action is None
    assert sum(1 for event in state.events if event.tool == "run_shell"
               and event.type == "observation"
               and "exit_code" in (event.data.get("value") or {})) == 1


def test_failed_audit_after_approval_blocks_actual_command(case):
    first = invoke(case, case.request, [command(case, "first")])
    case.manager.success = False
    result = invoke(case, approve(case, case.request, first), [FINISH])
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
        result = invoke(case, approve(case, case.request, first), [FINISH])
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
        ResearchRequest, RunBudget, RunPermissions, UserAnswer,
        WorkflowAgentRegistry,
    )
    from resagent2_orchestrator import (
        DeterministicWorkInterpreter,
        JsonRunStore, OrchestrationError, ResearchController, ResearchRun,
    )

    first = invoke(case, case.request, [command(case, "first")])
    resumed = approve(case, case.request, first)
    second = invoke(case, resumed, [command(case, "second")])
    assert second.status == "needs_user_input"
    old_question = first.control.question
    draft = second.control.question
    assert draft.action.arguments == command(case, "second")["arguments"]
    now = datetime.now(UTC)
    pending = draft
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
        interpreter=DeterministicWorkInterpreter(),
        scientific_port=SimpleNamespace(invoke=forbidden),
        compiler=SimpleNamespace(compile=forbidden),
        scheduler=SimpleNamespace(
            store=run_store, resume_task_in_place=forbidden,
            artifact_registry=ArtifactRegistry(case.root / "artifacts"),
        ),
        registry=WorkflowAgentRegistry(definitions=[]),
    )
    files = [*run_store.root.glob("*.json"), *(case.root / "sessions").glob("*.json")]
    before = {path: path.read_bytes() for path in files}
    with pytest.raises(OrchestrationError, match="answer does not match pending question"):
        controller.answer_question(
            case.request.run_id,
            UserAnswer(
                question_id=old_question.id,
                values={"approve": "yes"}, answered_at=now,
            ),
        )
    assert {path: path.read_bytes() for path in files} == before
    assert len(case.manager.audits) == 1
    assert (case.repo / "first.marker").read_text() == "executed\n"
    assert not (case.repo / "second.marker").exists()

