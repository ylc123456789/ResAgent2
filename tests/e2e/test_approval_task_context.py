"""An operation approval resumes execution without becoming task dialogue."""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from e2e.native_fixtures import tool_turns
from resagent2_components import PreparedEnvironment, ResourceLayout
from resagent2_contracts import (
    AgentOwner, ExecutionLimits, ResearchRequest, RunBudget, RunPermissions,
    TaskProposal, UserAnswer, WorkflowAgentDefinition, WorkflowAgentKind,
    WorkflowAgentRegistry, WorkflowProposal, WorkspaceAccess, WorkspaceSourceKind,
    WorkspaceSpec,
)
from resagent2_coding import NativeCodingAgent
from resagent2_experiment import NativeExperimentAgent
from resagent2_orchestrator import (
    CompilationResult, DeterministicWorkInterpreter, JsonRunStore, ModuleBinding,
    ResearchController, WorkflowScheduler,
)
from resagent2_orchestrator.handoffs import read_json
from resagent2_runtime import JsonSessionStore, ScriptedLLMClient
from resagent2_scientific import ScientificAgent


@pytest.mark.parametrize("kind", ["coding", "experiment"])
def test_business_yes_and_operation_approval_keep_distinct_consumers(
    tmp_path, monkeypatch, kind,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("executed.marker\n")
    (repo / "measure.py").write_text(
        "from pathlib import Path\n"
        "with Path('executed.marker').open('a') as f: f.write('executed\\n')\n"
    )
    for args in [
        ["init", "-q"], ["add", "."],
        ["-c", "user.name=Test", "-c", "user.email=test@example.com",
         "commit", "-qm", "baseline"],
    ]:
        subprocess.run(["git", *args], cwd=repo, check=True)

    # Environment discovery is isolated; the actual approved command runs a
    # real Python process and appends an observable, exact-once marker.
    conda = tmp_path / "conda"
    conda.write_text(
        f"#!{sys.executable}\nimport os, sys\n"
        "args = sys.argv[1:]\ncommand = args[args.index('-p') + 2:]\n"
        "os.environ['PATH'] = os.path.dirname(sys.executable) + os.pathsep + os.environ['PATH']\n"
        "os.execvp(command[0], command)\n"
    )
    conda.chmod(0o755)
    environment = PreparedEnvironment(
        env_id="resenv_test", prefix=Path(sys.prefix),
        python_version=".".join(map(str, sys.version_info[:3])),
    )
    audits = []

    def audit(current):
        assert current == environment
        audits.append(current)
        return {
            "success": True, "prefix_match": True, "pip_available": True,
            "python_version": current.python_version, "sys_prefix": str(current.prefix),
        }

    manager = SimpleNamespace(
        conda_exe=str(conda), inspect=lambda **kwargs: environment, audit=audit,
    )
    monkeypatch.setattr(f"resagent2_{kind}.agent.EnvironmentManager", lambda **kwargs: manager)
    command = {"tool": "run_shell", "arguments": {"command": "python measure.py"}}
    business_text = "Should this task continue? This answer is task guidance, not an operation approval."
    worker_client = ScriptedLLMClient(tool_turns([
        {"tool": "ask_user", "arguments": {
            "text": business_text, "requested_fields": ["approve"],
        }},
        command, command,
        {"tool": "finish", "arguments": {"report": "Executed the approved command once"}},
    ]))
    scientific_client = ScriptedLLMClient(tool_turns([
        {"tool": "request_work", "arguments": {
            "assessment": {"statement": "Need a recorded execution outcome"},
            "work_request": {"objective": "Execute the local marker script after asking the user", "expected_evidence": ["Recorded local execution"]},
        }},
        {"tool": "finish", "arguments": {
            "report": "The local execution task is complete",
            "artifacts": [{
                "kind": "scientific_opinion", "path": "opinion.json",
                "media_type": "application/json", "summary": "Execution outcome",
                "content": json.dumps({
                    "verdict": "not_applicable", "statement": "The approved local script executed once",
                    "evidence_artifact_ids": [], "limitations": ["No scientific hypothesis evaluated"],
                }),
            }],
        }},
    ]))
    owner = AgentOwner(kind)
    workflow_kind = WorkflowAgentKind(kind)
    sessions = JsonSessionStore(tmp_path / "sessions")
    layout = ResourceLayout(resource_root=tmp_path / "resources")
    agent_type = NativeCodingAgent if kind == "coding" else NativeExperimentAgent
    worker = agent_type(worker_client, store=sessions, resource_layout=layout)

    class Compiler:
        def compile(self, request, **kwargs):
            return CompilationResult(WorkflowProposal(
                work_request_id=request.id,
                tasks=[TaskProposal(
                    id="task_execute", work_request_id=request.id,
                    workflow_agent_kind=workflow_kind, workspace_id="ws_main",
                    instruction="Ask whether to continue, then execute measure.py",
                )],
            ))

    def controller():
        scheduler = WorkflowScheduler(
            bindings={workflow_kind: ModuleBinding(owner=owner, port=worker)},
            store=JsonRunStore(tmp_path / "runs"),
            artifact_root=tmp_path / "artifacts", data_root=tmp_path / "data",
            workspaces={"ws_main": WorkspaceSpec(
                workspace_id="ws_main", source_kind=WorkspaceSourceKind.LOCAL,
                location=str(repo), access=WorkspaceAccess(read_paths=["."], write_paths=["."]),
            )},
        )
        return ResearchController(
            interpreter=DeterministicWorkInterpreter(), compiler=Compiler(),
            scientific_port=ScientificAgent(
                scientific_client, store=JsonSessionStore(tmp_path / "scientific_sessions"),
                resource_layout=layout,
            ),
            scheduler=scheduler,
            registry=WorkflowAgentRegistry(definitions=[WorkflowAgentDefinition(
                workflow_agent_kind=workflow_kind, description="Execute a local script",
            )]),
        )

    run = controller().create_run("run_approval_context", ResearchRequest(
        goal="Record one local execution after business guidance and exact operation approval",
        budget=RunBudget(max_llm_calls=20, timeout_seconds=60),
        permissions=RunPermissions(execute_commands=True, prepare_environment=False),
        execution_limits=ExecutionLimits(max_tasks=1, max_attempts_per_task=1),
    ))
    assert run.status == "paused", run.terminal_error
    assert run.pending_question.action is None
    assert run.pending_question.requested_fields == ["approve"]
    session_id = run.workflow.tasks[0].attempts[0].session.id
    run = controller().answer_question(run.run_id, UserAnswer(
        question_id=run.pending_question.id, values={"approve": "yes"}, answered_at=datetime.now(UTC),
    ))
    assert run.status == "paused", run.terminal_error
    assert run.pending_question.action.tool == "run_shell"
    assert run.pending_question.action.arguments == command["arguments"]
    assert not (repo / "executed.marker").exists()
    assert audits == []  # Ordinary yes does not authorize an external effect.
    action = run.pending_question.action
    run = controller().answer_question(run.run_id, UserAnswer(
        question_id=run.pending_question.id, values={"approve": "yes"}, answered_at=datetime.now(UTC),
    ))
    assert run.status == "completed", run.terminal_error
    assert (repo / "executed.marker").read_text() == "executed\n"
    assert len(audits) == 1
    task = run.workflow.tasks[0]
    assert len(task.attempts) == 1 and task.attempts[0].session.id == session_id
    assert len(run.answers) == 2
    assert run.answers[0].action is None and run.answers[1].action == action

    answer_refs = [ref for ref in run.artifacts.values() if ref.kind == "answer"]
    assert len(answer_refs) == 2
    bodies = [read_json(ref) for ref in answer_refs]
    assert {body["question_id"] for body in bodies} == {answer.question_id for answer in run.answers}
    assert next(body for body in bodies if body["action"] is not None)["action"] == action.model_dump(mode="json")
    # Inspect the task section actually sent before execution and before finish.
    # The operation remains in durable control/history, not in task requirements.
    for context in worker_client.contexts[1:]:
        task_text = context.text.split("## task\n", 1)[1].split("\n\n## ", 1)[0]
        dialogue = json.loads(task_text)["user_answers"]
        assert len(dialogue) == 1
        assert dialogue[0]["question"] == business_text
        assert dialogue[0]["values"] == {"approve": "yes"}
        assert dialogue[0]["question_id"] == run.answers[0].question_id

    state = sessions.load(session_id)
    assert state.pending_action is None
    assert len(state.tool_turns) == 4
    assert all(set(turn.tool_results) == {call.id for call in turn.tool_calls}
               and turn.executing_call_id is None for turn in state.tool_turns)
    receipts = [json.loads(next(iter(turn.tool_results.values()))) for turn in state.tool_turns]
    assert sum(receipt.get("value", {}).get("exit_code") == 0 for receipt in receipts
               if isinstance(receipt.get("value"), dict)) == 1
    assert run.llm_calls_used == len(worker_client.contexts) + len(scientific_client.contexts) == 6
