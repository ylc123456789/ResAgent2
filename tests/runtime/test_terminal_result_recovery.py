"""Terminal result and Session status are one durable checkpoint for all Agents."""

import json
import subprocess

import pytest

from e2e.native_fixtures import tool_turns
from resagent2_components import ResourceLayout
from resagent2_contracts import AgentPermissions, AgentRequest, TaskBudget, WorkspaceAccess, WorkspaceGrant, task_session_id, scientific_session_id
from resagent2_coding import NativeCodingAgent
from resagent2_experiment import NativeExperimentAgent
from resagent2_runtime import JsonSessionStore, ScriptedLLMClient
from resagent2_scientific import ScientificAgent


class _ProcessStopped(BaseException):
    pass


class _StopAfterTerminalSave(JsonSessionStore):
    def save(self, state):
        super().save(state)
        if state.terminal_result is not None:
            raise _ProcessStopped()


@pytest.mark.parametrize("kind,status", [
    ("scientific", "completed"), ("coding", "completed"), ("experiment", "completed"),
    ("coding", "failed"), ("experiment", "failed"),
])
def test_reopen_after_terminal_save_returns_the_same_result_without_model_or_tool_replay(tmp_path, kind, status):
    session_root = tmp_path / "sessions"
    report = " \nOriginal professional report, including whitespace.\n "
    args = {"report": report, "status": status}
    request_fields = {}
    if kind == "scientific":
        args["artifacts"] = [{
            "kind": "scientific_opinion", "path": "opinion.json", "summary": "Original judgment",
            "media_type": "application/json", "content": json.dumps({
                "verdict": "inconclusive", "statement": "No measurements requested",
            }),
        }]
        session_id = scientific_session_id("run_terminal")
    else:
        root = tmp_path / "repo"
        root.mkdir()
        (root / "source.py").write_text("VALUE = 1\n")
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "add", "source.py"], cwd=root, check=True)
        subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.com",
                        "commit", "-qm", "baseline"], cwd=root, check=True)
        request_fields = {
            "task_id": "task_terminal", "attempt_number": 1, "workspace_id": "ws_terminal",
            "workspace": WorkspaceGrant(root=str(root), source="local", access=WorkspaceAccess(read_paths=["."], write_paths=[])),
        }
        session_id = task_session_id("run_terminal", "task_terminal", 1)
    request = AgentRequest(
        run_id="run_terminal", agent=kind, instruction="Explain the available evidence",
        permissions=AgentPermissions(), budget=TaskBudget(max_llm_calls=5, timeout_seconds=60), **request_fields,
    )
    client = ScriptedLLMClient(tool_turns([{"tool": "finish", "arguments": args}]))
    agent_class = {"scientific": ScientificAgent, "coding": NativeCodingAgent, "experiment": NativeExperimentAgent}[kind]
    layout = ResourceLayout(resource_root=tmp_path / "resources")
    with pytest.raises(_ProcessStopped):
        agent_class(client, store=_StopAfterTerminalSave(session_root), resource_layout=layout).invoke(request)
    saved = JsonSessionStore(session_root).load(session_id)
    assert saved.terminal_result is not None
    assert saved.terminal_result.report == report
    assert saved.terminal_result.status == status
    assert saved.memory.get("_invocation_results", {}) == {}
    receipt_before = saved.tool_turns[0].tool_results.copy()

    resumed_client = ScriptedLLMClient([])
    resumed_request = request if kind == "scientific" else request.model_copy(update={"parent_session_id": session_id})
    final = agent_class(resumed_client, store=JsonSessionStore(session_root), resource_layout=layout).invoke(resumed_request)
    assert final.status == status
    assert final.report == report
    assert final.llm_calls == 0
    assert resumed_client.contexts == []
    after = JsonSessionStore(session_root).load(session_id)
    assert after.llm_calls_used == saved.llm_calls_used == 1
    assert after.tool_turns[0].tool_results == receipt_before
    assert len(after.tool_turns) == 1
    if kind == "scientific":
        assert final.artifacts[0].content == args["artifacts"][0]["content"]
        assert [item.kind for item in final.artifacts].count("observation_trace") == 1
