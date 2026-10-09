"""Native tool flow preserves the timing of reads across a successful edit."""

from e2e.native_fixtures import tool_turns

from resagent2_contracts import AgentPermissions

from copy import deepcopy
import json
import subprocess

import pytest

from resagent2_components import ResourceLayout
from resagent2_components.context import workspace_context
from resagent2_coding import NativeCodingAgent
from resagent2_contracts import (
    WorkflowAgentKind, ModuleStatus, AgentRequest, TaskBudget,
    WorkspaceGrant, WorkspaceAccess, WorkspaceSourceKind, task_session_id,
)
from resagent2_runtime import ContextComposer, InMemorySessionStore
from resagent2_runtime.tool_calling import native_input_text


class _ActionClient:
    tool_session_key = "test-native-tools/v1"

    def __init__(self, actions):
        self.actions = iter(actions)
        self.contexts = []
        self.schemas = []
        self.histories = []
        self.input_limits = []

    def next_tool_call(self, context, schemas, turns, *, max_input_tokens):
        self.contexts.append(context)
        self.schemas.append(deepcopy(schemas))
        self.histories.append([turn.model_copy(deep=True) for turn in turns])
        self.input_limits.append(max_input_tokens)
        return tool_turns([next(self.actions)])[0]


def _workspace_reads(context):
    section = context.text.split("## file_reads\n", 1)[1].split("\n\n## ", 1)[0]
    return {"file_snippets": json.loads(section.split("\n", 1)[1])["snippets"]}


@pytest.mark.parametrize("edit_succeeds", [True, False])
def test_native_coding_read_history_marks_only_successful_later_edits(tmp_path, edit_succeeds):
    root = tmp_path / "repo"
    root.mkdir()
    source = "def score():\n    total = 1\n    return totla\n\nprint(score())\n"
    (root / "train.py").write_text(source, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "add", "train.py"], cwd=root, check=True)
    subprocess.run([
        "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "-qm", "read history fixture",
    ], cwd=root, check=True)
    actions = [
        {"tool": "read_file", "arguments": {
            "path": "train.py", "start_line": 1, "end_line": 3,
        }},
        {"tool": "replace_text", "arguments": {
            "path": "train.py", "old_text": "return totla" if edit_succeeds else "not present",
            "new_text": "return total",
        }},
        {"tool": "read_file", "arguments": {
            "path": "train.py", "start_line": 2, "end_line": 5,
        }},
        # Later real tool calls must retain earlier reads and the edit in
        # both the working set and the paired native protocol history.
        *[{"tool": "list_files", "arguments": {"path": "."}} for _ in range(7)],
        {"tool": "ask_user", "arguments": {
            "text": "Continue with verification?", "requested_fields": ["approval"],
        }},
    ]
    client = _ActionClient(actions)
    store = InMemorySessionStore()
    request = AgentRequest(run_id='run_read_history', task_id='task_read_history', attempt_number=1, agent='coding', instruction='Read the function before editing it', workspace=WorkspaceGrant(root=str(root), source=WorkspaceSourceKind.LOCAL, access=WorkspaceAccess(read_paths=['.'], write_paths=['.'])), workspace_id='ws_read_history', output_dir=str(tmp_path / 'outputs'), budget=TaskBudget(max_llm_calls=20, timeout_seconds=30), permissions=AgentPermissions(execute_commands=True, prepare_environment=True))
    agent = NativeCodingAgent(
        client, store=store,
        resource_layout=ResourceLayout(resource_root=tmp_path / "resources"),
    )
    result = agent.invoke(request)
    assert result.status == ModuleStatus.NEEDS_USER_INPUT, result.model_dump(mode="json")
    assert len(client.contexts) == len(actions)
    state = store.load(task_session_id(request.run_id, request.task_id, 1))
    observations = [event for event in state.events if event.type == "observation"]
    reads = [event for event in observations if event.tool == "read_file"]
    snippets = _workspace_reads(client.contexts[-1])["file_snippets"]
    assert len(snippets) == 2
    assert [item["observed_at"] for item in snippets] == [event.sequence for event in reads]
    assert snippets[0]["content"] == "def score():\n    total = 1\n    return totla\n"
    assert "modified_after_read_at" not in snippets[1]
    if edit_succeeds:
        edit = next(event for event in observations if event.tool == "replace_text")
        assert edit.data["ok"] is True
        assert snippets[0]["modified_after_read_at"] == edit.sequence
        assert "return total" in snippets[1]["content"]
        assert "return totla" not in snippets[1]["content"]
    else:
        assert "modified_after_read_at" not in snippets[0]
        assert "return totla" in snippets[1]["content"]
    history = client.histories[-1]
    assert len(history) == len(actions) - 1
    assert [call.name for turn in history for call in turn.tool_calls] == [
        action["tool"] for action in actions[:-1]
    ]
    receipts = []
    for turn in history:
        assert set(turn.tool_results) == {call.id for call in turn.tool_calls}
        receipts.append(json.loads(turn.tool_results[turn.tool_calls[0].id]))
    assert receipts[0]["value"]["content"] == snippets[0]["content"]
    assert receipts[2]["value"]["content"] == snippets[1]["content"]
    assert [receipts[index]["observed_at"] for index in (0, 2)] == [
        event.sequence for event in reads
    ]
    assert receipts[1]["ok"] is edit_succeeds
    names = {schema["function"]["name"] for schema in client.schemas[-1]}
    assert {"read_file", "replace_text", "list_files", "ask_user"} <= names
    context = client.contexts[-1]
    measured = ContextComposer.estimate_tokens(native_input_text(context.text, client.schemas[-1], history))
    assert measured == context.estimated_tokens
    assert measured <= client.input_limits[-1]
    assert "recent_observations" not in context.included_sections
    assert (root / "train.py").read_text(encoding="utf-8") == (
        source.replace("return totla", "return total") if edit_succeeds else source
    )
    # Timing labels belong to the rendered projection, not persisted Tool
    # observations: rendering must never rewrite the append-only evidence.
    before = [event.model_dump(mode="json") for event in state.events]
    workspace_context(state)
    assert [event.model_dump(mode="json") for event in state.events] == before
    assert all("observed_at" not in event.data["value"] for event in reads)
    assert all("modified_after_read_at" not in event.data["value"] for event in reads)
