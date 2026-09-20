"""Unified Coding invocation, workspace ownership and operation authorization."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from resagent2_contracts import (
    AgentOwner, AgentPermissions, AgentRequest, ModuleStatus,
    TaskBudget, WorkspaceGrant, WorkspaceMode, WorkspaceSourceKind,
)
from resagent2_coding import CodingAction, NativeCodingAgent
from resagent2_runtime import ScriptedLLMClient


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    (root / "util.py").write_text("def add(a, b):\n    return a + b\n")
    subprocess.run(["git", "add", "util.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)


def request(root: Path, *, writable=False, task_id="task_coding", **updates) -> AgentRequest:
    values = dict(
        run_id="run_coding", task_id=task_id, attempt_number=1, agent=AgentOwner.CODING,
        instruction="Inspect add and make only needed changes",
        budget=TaskBudget(max_llm_calls=8, timeout_seconds=30),
        workspace=WorkspaceGrant(
            root=str(root), mode=WorkspaceMode.READ_WRITE if writable else WorkspaceMode.READ_ONLY,
            allowed_paths=["."], source=WorkspaceSourceKind.LOCAL,
        ),
        workspace_id="ws_test", output_dir=str(root.parent / "output"),
    )
    values.update(updates)
    return AgentRequest(**values)


def finish(report="add is implemented in util.py", **kwargs):
    return {"tool": "finish", "arguments": {"report": report, **kwargs}}


def edit():
    return {"tool": "replace_text", "arguments": {
        "path": "util.py", "old_text": "def add(a, b):",
        "new_text": "def add(a, b):\n    \"\"\"Return the sum.\"\"\"",
    }}


@pytest.mark.parametrize("writable", [False, True])
def test_same_protocol_can_explain_without_edits(tmp_path, writable):
    init_repo(tmp_path)
    before = (tmp_path / "util.py").read_text()
    client = ScriptedLLMClient([
        {"tool": "read_file", "arguments": {"path": "util.py"}}, finish(),
    ])
    result = NativeCodingAgent(client).invoke(request(tmp_path, writable=writable))
    assert result.status == ModuleStatus.COMPLETED
    assert result.report == "add is implemented in util.py"
    assert (tmp_path / "util.py").read_text() == before
    assert not any(item.kind == "code_patch" for item in result.artifacts)


def test_read_only_can_deliver_report_content(tmp_path):
    init_repo(tmp_path)
    artifact = {"kind": "module_report", "path": "explanation.md",
                "media_type": "text/markdown", "summary": "Code explanation",
                "content": "The function returns a + b."}
    result = NativeCodingAgent(ScriptedLLMClient([finish(artifacts=[artifact])])).invoke(request(tmp_path))
    assert result.status == ModuleStatus.COMPLETED
    assert any(item.kind == "module_report" and item.content == artifact["content"] for item in result.artifacts)
    assert not (tmp_path / "explanation.md").exists()


def test_edit_uses_same_finish_and_derives_patch(tmp_path):
    init_repo(tmp_path)
    result = NativeCodingAgent(ScriptedLLMClient([edit(), finish("Added a docstring; tests not run")])).invoke(
        request(tmp_path, writable=True),
    )
    assert result.status == ModuleStatus.COMPLETED
    patch = next(item for item in result.artifacts if item.kind == "code_patch")
    assert "Return the sum" in patch.content
    assert {item.path for item in result.artifacts if item.kind == "code_change"} == {"util.py"}
    assert not any(item.kind == "verification_result" for item in result.artifacts)


def test_new_file_becomes_code_artifact(tmp_path):
    init_repo(tmp_path)
    result = NativeCodingAgent(ScriptedLLMClient([
        {"tool": "create_file", "arguments": {"path": "new.py", "content": "VALUE = 1\n"}},
        finish("Added a constant"),
    ])).invoke(request(tmp_path, writable=True))
    assert result.status == ModuleStatus.COMPLETED
    assert "new.py" in {item.path for item in result.artifacts}


def test_two_tasks_isolate_changed_files(tmp_path):
    init_repo(tmp_path)
    first = NativeCodingAgent(ScriptedLLMClient([edit(), finish()])).invoke(request(tmp_path, writable=True))
    assert first.status == ModuleStatus.COMPLETED
    second = NativeCodingAgent(ScriptedLLMClient([
        {"tool": "create_file", "arguments": {"path": "second.py", "content": "X = 2\n"}}, finish(),
    ])).invoke(request(tmp_path, writable=True, task_id="task_second"))
    assert {item.path for item in second.artifacts if item.kind == "code_change"} == {"second.py"}
    assert "Return the sum" not in next(item.content for item in second.artifacts if item.kind == "code_patch")


def test_read_only_inspects_shared_dirty_workspace(tmp_path):
    init_repo(tmp_path)
    (tmp_path / "util.py").write_text("# previous accepted change\n")
    result = NativeCodingAgent(ScriptedLLMClient([
        {"tool": "read_file", "arguments": {"path": "./util.py"}}, finish(),
    ])).invoke(request(tmp_path))
    assert result.status == ModuleStatus.COMPLETED


def test_write_action_has_one_schema_but_read_only_grant_denies_it(tmp_path):
    init_repo(tmp_path)
    CodingAction.model_validate(edit())
    result = NativeCodingAgent(ScriptedLLMClient([edit()])).invoke(request(tmp_path))
    assert result.status == ModuleStatus.FAILED
    assert "Return the sum" not in (tmp_path / "util.py").read_text()


def test_disallowed_verification_command_does_not_execute(tmp_path):
    init_repo(tmp_path)
    result = NativeCodingAgent(ScriptedLLMClient([
        {"tool": "run_verification", "arguments": {"commands": ["touch unauthorized"]}},
    ])).invoke(request(tmp_path, writable=True))
    assert result.status == ModuleStatus.FAILED
    assert not (tmp_path / "unauthorized").exists()


def test_process_permission_is_enforced(tmp_path):
    init_repo(tmp_path)
    result = NativeCodingAgent(ScriptedLLMClient([
        {"tool": "run_verification", "arguments": {"commands": ["python -m pytest"]}},
    ])).invoke(request(tmp_path, writable=True, permissions=AgentPermissions(execute_commands=False)))
    assert result.status == ModuleStatus.FAILED


def test_failed_call_retains_diagnostic_patch(tmp_path):
    init_repo(tmp_path)
    result = NativeCodingAgent(ScriptedLLMClient([edit()])).invoke(request(tmp_path, writable=True))
    assert result.status == ModuleStatus.FAILED
    patch = next(item for item in result.artifacts if item.kind == "code_patch")
    assert patch.metadata["diagnostic"]
    assert "Return the sum" in patch.content
    assert result.error.retryable is False


@pytest.mark.parametrize("kind", ["verification_result", "execution_record", "observation_trace", "literature_search"])
def test_model_cannot_fabricate_system_record(tmp_path, kind):
    init_repo(tmp_path)
    result = NativeCodingAgent(ScriptedLLMClient([finish(artifacts=[{
        "kind": kind, "path": "fake.json", "summary": "fake pass",
        "media_type": "application/json", "content": '{"passed": true}',
    }])])).invoke(request(tmp_path))
    assert result.status == ModuleStatus.FAILED


def test_missing_workspace_is_blocked(tmp_path):
    result = NativeCodingAgent(ScriptedLLMClient([])).invoke(request(tmp_path, workspace=None))
    assert result.status == ModuleStatus.BLOCKED
