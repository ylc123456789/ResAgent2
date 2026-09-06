"""Native tool flow preserves the timing of reads across a successful edit."""

import json
import subprocess

import pytest

from resagent2_capabilities import ResourceLayout
from resagent2_capabilities.workspace_context import workspace_context
from resagent2_coding import NativeCodingAgent
from resagent2_contracts import (
    Capability, CodeModifyInput, ModuleStatus, ModuleTaskRequest, TaskBudget,
    WorkspaceGrant, WorkspaceMode, WorkspaceSourceKind, task_session_id,
)
from resagent2_runtime import InMemorySessionStore


class _ActionClient:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.contexts = []

    def next_action(self, context, action_type):
        self.contexts.append(context)
        return next(self.actions)


def _workspace_reads(context):
    section = context.text.split("## workspace_reads\n", 1)[1].split("\n\n## ", 1)[0]
    return json.loads(section.split("\n", 1)[1])


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
        # The edit must remain explainable after it rotates out of Runtime's
        # short recent-observation window. These use the real listing Tool.
        *[{"tool": "list_files", "arguments": {"path": "."}} for _ in range(7)],
        {"tool": "ask_user", "arguments": {
            "text": "Continue with verification?", "requested_fields": ["approval"],
            "reason": "Stop at the context boundary without installing an environment",
        }},
    ]
    client = _ActionClient(actions)
    store = InMemorySessionStore()
    request = ModuleTaskRequest(
        run_id="run_read_history", task_id="task_read_history", attempt_number=1,
        capability=Capability.CODE_MODIFY, goal="Fix the misspelled return variable",
        inputs=CodeModifyInput(instructions="Read the function before editing it"),
        workspace=WorkspaceGrant(
            root=str(root), mode=WorkspaceMode.READ_WRITE,
            allowed_paths=["."], source=WorkspaceSourceKind.LOCAL,
        ),
        workspace_id="ws_read_history", output_dir=str(tmp_path / "outputs"),
        budget=TaskBudget(max_steps=20, max_llm_calls=20, timeout_seconds=30),
    )
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
    history = client.contexts[-1].text.split("## recent_observations\n", 1)[1].split("\n\n## ", 1)[0]
    assert "replace_text" not in history
    assert "read_file" not in history
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
