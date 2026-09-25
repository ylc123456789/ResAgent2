"""Native completion feedback keeps one invocation, Attempt and Session."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from resagent2_coding import NativeCodingAgent
from resagent2_experiment import NativeExperimentAgent
from resagent2_contracts import (
    AgentOwner, ErrorCode, ExecutionLimits, ResearchRequest, RunBudget,
    RunPermissions, TaskProposal, WorkflowProposal, WorkspaceAccess, WorkspaceSpec,
)
from resagent2_orchestrator import (
    InMemoryRunStore, ModuleBinding, ResearchRun, WorkflowScheduler,
)
from resagent2_runtime import NativeToolCall, ToolCallTurn


def output(path="metrics.json", **fields):
    return dict(kind="data", path=path, summary="Recorded results",
                media_type="application/json", **fields)


class NativeClient:
    tool_session_key = "completion-feedback-test"

    def __init__(self, submissions, *, prepare=None):
        self.submissions = submissions
        self.prepare = prepare
        self.contexts = []

    def next_tool_call(self, context, schemas, turns, **kwargs):
        index = len(self.contexts)
        self.contexts.append(context)
        if self.prepare:
            self.prepare(index)
        return ToolCallTurn(tool_calls=[NativeToolCall(
            id=f"finish_{index}", name="finish", arguments=json.dumps({
                "report": "Analysis ready", "artifacts": self.submissions[index],
            }),
        )])


def execute(tmp_path, kind, submissions, *, budget=8, prepare=None):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    (workspace / "metrics.json").write_text('{"accuracy": 0.9}')
    for command in [
        ["git", "add", "metrics.json"],
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com",
         "commit", "-qm", "baseline"],
    ]:
        subprocess.run(command, cwd=workspace, check=True)
    client = NativeClient(submissions, prepare=prepare)
    agent = (NativeCodingAgent if kind == "coding" else NativeExperimentAgent)(client)
    requests = []

    class Port:
        def invoke(self, request):
            requests.append(request)
            return agent.invoke(request)

    scheduler = WorkflowScheduler(
        bindings={kind: ModuleBinding(owner=AgentOwner(kind), port=Port())},
        store=InMemoryRunStore(), artifact_root=tmp_path / "artifacts",
        data_root=tmp_path / "data", workspaces={"ws_test": WorkspaceSpec(
            workspace_id="ws_test", source_kind="local", location=str(workspace),
            access=WorkspaceAccess(read_paths=["."], write_paths=[]),
        )},
    )
    now = datetime.now(UTC)
    run = ResearchRun(
        run_id="run_completion_feedback", status="running",
        request=ResearchRequest(
            goal="Analyze the provided metrics", permissions=RunPermissions(),
            budget=RunBudget(max_llm_calls=budget, timeout_seconds=30),
            execution_limits=ExecutionLimits(max_tasks=1, max_attempts_per_task=2),
        ), workspaces=scheduler._resolve_workspaces("run_completion_feedback"),
        created_at=now, updated_at=now,
    )
    scheduler.store.save(run)
    scheduler.accept_proposal(run.run_id, WorkflowProposal(
        work_request_id="work_1", tasks=[TaskProposal(
            id="task_analysis", work_request_id="work_1",
            workflow_agent_kind=kind, instruction="Analyze the provided metrics",
        )],
    ))
    final = scheduler.run_until_stable(run.run_id)
    task = final.workflow.tasks[0]
    assert len(requests) == len(task.attempts) == 1
    attempt = task.attempts[0]
    state = agent.loop.store.load(attempt.session.id)
    assert (state.run_id, state.task_id, state.attempt_number) == (run.run_id, task.id, 1)
    assert final.llm_calls_used == state.llm_calls_used == len(client.contexts)
    return final, task, state, client


@pytest.mark.parametrize("kind", ["coding", "experiment"])
@pytest.mark.parametrize("mistake,code", [
    ([output("wrong-name.json")], "artifact_path_missing"),
    ([output(output_name="scores"), output(output_name="scores")], "duplicate_output_name"),
])
def test_finish_correction_reaches_registration_in_same_attempt(tmp_path, kind, mistake, code):
    run, task, state, client = execute(tmp_path, kind, [mistake, [output()]])
    assert task.status == state.status == "completed"
    assert len(client.contexts) == 2
    assert code in client.contexts[1].text
    assert state.runtime_feedback is None
    assert len(state.tool_turns) == 2
    assert all(len(turn.tool_results) == 1 for turn in state.tool_turns)
    refs = [run.artifacts[key] for key in task.attempts[0].artifact_ids]
    assert len(refs) == 1
    assert Path(refs[0].uri.removeprefix("file://")).read_text() == '{"accuracy": 0.9}'
    assert refs[0].metadata["source_path"] == "metrics.json"


@pytest.mark.parametrize("kind", ["coding", "experiment"])
def test_output_directory_and_registry_use_the_same_source(tmp_path, kind):
    # The native finalizer must recognize the scheduler-controlled attempt root.
    def prepare(index):
        # Obtain the actual root from the existing layout, never guess its shape.
        from resagent2_orchestrator.layout import RunLayout
        attempt_dir = RunLayout(tmp_path / "data").attempt_dir(
            "run_completion_feedback", "task_analysis", 1,
        )
        attempt_dir.mkdir(parents=True, exist_ok=True)
        (attempt_dir / "result.json").write_text("{}")
    run, task, _, _ = execute(tmp_path, kind, [[output("result.json")]], prepare=prepare)
    assert task.status == "completed"
    ref = run.artifacts[task.attempts[0].artifact_ids[0]]
    assert ref.metadata["source_root"] == "output_dir"


@pytest.mark.parametrize("kind", ["coding", "experiment"])
def test_invalid_finish_keeps_feedback_when_budget_exhausts(tmp_path, kind):
    run, task, state, client = execute(tmp_path, kind, [[output("absent.json")]], budget=1)
    assert task.status == state.status == "failed"
    assert task.attempts[0].error.code == ErrorCode.BUDGET_EXHAUSTED
    assert "artifact_path_missing" in state.runtime_feedback.summary
    assert not task.attempts[0].artifact_ids
    assert len(state.tool_turns) == 1
    assert "absent.json" in state.tool_turns[0].tool_calls[0].arguments


@pytest.mark.parametrize("kind", ["coding", "experiment"])
def test_escaped_candidate_does_not_become_correctable_success(tmp_path, kind):
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    def prepare(index):
        (tmp_path / "workspace" / "escape.json").symlink_to(outside)
    run, task, state, client = execute(
        tmp_path, kind, [[output("escape.json")], [output()]], prepare=prepare,
    )
    assert task.status == state.status == "failed"
    assert task.attempts[0].error.code == ErrorCode.CONTRACT_ERROR
    assert "outside" in task.attempts[0].error.message
    assert len(client.contexts) == 1
    assert not task.attempts[0].artifact_ids
