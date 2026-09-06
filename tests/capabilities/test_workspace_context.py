"""Context projects tool state and keeps file/artifact reading budgets separate."""

from datetime import UTC, datetime
import json
from types import SimpleNamespace

import pytest

from resagent2_capabilities import (
    AuditEnvInput, AuditEnvTool, EnvironmentBinding, PreparedEnvironment,
    RunSetupInput, RunSetupTool, workspace_context,
)
from resagent2_capabilities.workspace_context import READ_CONTEXT_CHARS_PER_KIND
from resagent2_contracts import (
    AgentOwner, Capability, CodeModifyInput, ExperimentRunInput, ModuleTaskRequest, TaskBudget,
)
from resagent2_runtime import AgentEvent, AgentState, ContextComposer
from resagent2_coding.context import build_context as coding_context, MODIFY_PROMPT
from resagent2_experiment.context import build_context as experiment_context, EXPERIMENT_PROMPT


def _state():
    now = datetime.now(UTC)
    return AgentState(
        session_id="session_context", agent_name="test", owner=AgentOwner.EXPERIMENT,
        run_id="run_context", task_id="task_context", attempt_number=1,
        created_at=now, updated_at=now,
    )

def _observe(state, tool, value, *, ok=True):
    state.events.append(AgentEvent(
        sequence=len(state.events) + 1, step=len(state.events) + 1,
        type="observation", tool=tool, data={"ok": ok, "value": value},
        created_at=datetime.now(UTC),
    ))

def _binding(tmp_path, *, restored=True):
    current = PreparedEnvironment(env_id="env_test", prefix=tmp_path, python_version="3.12.13")
    manager = SimpleNamespace(
        conda_exe="conda", inspect=lambda **_: current if restored else None,
        audit=lambda _: {"success": True},
    )
    return EnvironmentBinding(manager, run_id="run_context", workspace_id="workspace_context")

def _environment(state, binding):
    section = next(s for s in workspace_context(state, binding=binding) if s.name == "environment")
    return json.loads(section.content)

def _reads(state):
    section = next(s for s in workspace_context(state) if s.name == "workspace_reads")
    return json.loads(section.content.split("\n", 1)[1])

def test_restored_environment_is_visible_but_not_certified(tmp_path):
    state = _state()
    state.memory["environment"] = {"env_id": "stale_env", "certified": True}
    binding = _binding(tmp_path)
    assert _environment(state, binding) == {
        "prepared": True, "certified": False, "required_python": None,
        "env_id": "env_test", "prefix": str(tmp_path), "python_version": "3.12.13",
    }
    assert _environment(state, _binding(tmp_path, restored=False))["prepared"] is False

def test_audit_remains_visible_after_observation_history_rotates(tmp_path):
    state = _state()
    binding = _binding(tmp_path)
    assert AuditEnvTool(binding).execute(state, AuditEnvInput()).ok
    for _ in range(10):
        _observe(state, "list_files", {"path": ".", "paths": ["train.py"]})
    assert _environment(state, binding)["certified"] is True
    # No env_audit/environment memory copies are required for this decision.
    assert state.memory == {}

def test_setup_failure_immediately_invalidates_displayed_audit(tmp_path):
    state = _state()
    binding = _binding(tmp_path)
    AuditEnvTool(binding).execute(state, AuditEnvInput())
    state.memory["env_audit"] = {"success": True}
    def fail(*args, **kwargs):
        assert _environment(state, binding)["certified"] is False
        raise OSError("installation interrupted")
    tool = RunSetupTool(SimpleNamespace(run=fail), binding, log_dir=str(tmp_path), timeout_seconds=10)
    with pytest.raises(OSError, match="interrupted"):
        tool.execute(state, RunSetupInput(command="python -m pip install example"))
    assert _environment(state, binding)["certified"] is False
    # A new process must also re-audit instead of believing old Session memory.
    assert _environment(state, _binding(tmp_path))["certified"] is False

def test_artifact_content_survives_short_history_without_a_second_cache():
    state = _state()
    text = "a" * 900 + "IMPORTANT_PATCH_IN_THE_MIDDLE" + "b" * 900
    _observe(state, "read_artifact", {
        "artifact_id": "artifact_patch", "content": text, "truncated": False,
    })
    for _ in range(10):
        _observe(state, "list_files", {"path": ".", "paths": ["train.py"]})
    assert _reads(state)["artifact_snippets"][0]["content"] == text
    assert "read_artifact_summaries" not in state.memory

def test_artifact_does_not_evict_file_ranges_or_dependencies():
    state = _state()
    state.memory["read_paths"] = ["requirements.txt", "train.py"]
    _observe(state, "read_file", {"path": "requirements.txt", "content": "torch"})
    _observe(state, "read_artifact", {"artifact_id": "artifact_patch", "content": "A" * 6000})
    for line in (10, 50):
        _observe(state, "read_file", {
            "path": "train.py", "start_line": line, "end_line": line + 5,
            "content": "B" * 1000, "truncated": False,
        })
    reads = _reads(state)
    files = reads["file_snippets"]
    assert [s.get("start_line") for s in files[1:]] == [10, 50]
    assert files[0]["content"] == "torch"
    assert reads["artifact_snippets"][0]["content"] == "A" * 6000
    for name in ("file_snippets", "artifact_snippets"):
        assert sum(len(s["content"]) for s in reads[name]) <= READ_CONTEXT_CHARS_PER_KIND
    # Forgotten content does not imply that the dependency file was never read.
    assert "requirements.txt" in reads["previously_read_files"]


@pytest.mark.parametrize("newest_tool,newest_key", [
    ("read_file", "path"), ("read_artifact", "artifact_id"),
])
def test_many_reads_of_one_kind_do_not_evict_the_other(newest_tool, newest_key):
    state = _state()
    _observe(state, "read_file", {"path": "keep.py", "content": "file context"})
    _observe(state, "read_artifact", {"artifact_id": "artifact_keep", "content": "artifact context"})
    for index in range(10):
        _observe(state, newest_tool, {newest_key: str(index), "content": "X" * 2000})
    reads = _reads(state)
    other = "artifact_snippets" if newest_tool == "read_file" else "file_snippets"
    expected = "artifact context" if newest_tool == "read_file" else "file context"
    assert reads[other][0]["content"] == expected
    for name in ("file_snippets", "artifact_snippets"):
        assert sum(len(s["content"]) for s in reads[name]) <= READ_CONTEXT_CHARS_PER_KIND


def test_each_kind_is_truncated_at_its_own_content_limit():
    state = _state()
    for tool, key in (("read_file", "path"), ("read_artifact", "artifact_id")):
        _observe(state, tool, {
            key: "source", "start_line": 10, "end_line": 80,
            "content": "X" * 8000, "truncated": False,
        })
    reads = _reads(state)
    for name in ("file_snippets", "artifact_snippets"):
        snippet = reads[name][0]
        assert len(snippet["content"]) == READ_CONTEXT_CHARS_PER_KIND
        assert snippet["truncated"] is True
        assert snippet["context_truncated"] is True
        assert (snippet["start_line"], snippet["end_line"]) == (10, 80)
    assert sum(len(s["content"]) for name in ("file_snippets", "artifact_snippets") for s in reads[name]) == 12000

@pytest.mark.parametrize("builder,capability,inputs,prompt", [
    (coding_context, Capability.CODE_MODIFY, CodeModifyInput(instructions="Make a bounded change"), MODIFY_PROMPT),
    (experiment_context, Capability.EXPERIMENT_RUN, ExperimentRunInput(instructions="Run the script"), EXPERIMENT_PROMPT),
], ids=["coding", "experiment"])
def test_both_agents_use_shared_context_within_existing_budget(tmp_path, builder, capability, inputs, prompt):
    state = _state()
    binding = _binding(tmp_path)
    _observe(state, "read_file", {"path": "train.py", "content": "X" * 6000})
    _observe(state, "read_artifact", {"artifact_id": "artifact_patch", "content": "Y" * 6000})
    request = ModuleTaskRequest(
        run_id="run_context", task_id="task_context", attempt_number=1,
        capability=capability, inputs=inputs, goal="Bounded task",
        budget=TaskBudget(max_steps=10, max_llm_calls=10, timeout_seconds=30),
    )
    sections = builder(request, state, binding=binding)
    assert {s.name for s in sections} >= {"environment", "workspace_reads"}
    context = ContextComposer().compose(prompt, sections, max_tokens=8192)
    assert context.estimated_tokens <= 8192
    assert "workspace_reads" in context.included_sections


def test_read_metadata_cannot_bypass_working_set_budget():
    state = _state()
    state.memory["read_paths"] = ["directory/" + "x" * 100 + str(n) for n in range(100)]
    _observe(state, "read_artifact", {
        "artifact_id": "artifact_patch", "content": "actual content",
        "summary": "unbounded metadata " * 5000, "kind": "code_patch",
    })
    data = _reads(state)
    assert "summary" not in data["artifact_snippets"][0]
    assert sum(map(len, data["previously_read_files"])) <= 600
    assert set(data["previously_read_files"]) <= set(state.memory["read_paths"])


@pytest.mark.parametrize("read_path,edit_path", [
    ("train.py", "train.py"), ("./train.py", "train.py"),
    ("src\\train.py", "./src/train.py"),
])
def test_old_file_snippets_are_retained_and_marked_after_successful_edit(read_path, edit_path):
    state = _state()
    _observe(state, "read_file", {
        "path": read_path, "start_line": 1, "end_line": 100,
        "content": "old implementation", "truncated": False,
    })
    _observe(state, "read_file", {"path": "requirements.txt", "content": "torch"})
    _observe(state, "replace_text", {"path": edit_path})
    _observe(state, "read_file", {
        "path": edit_path, "start_line": 40, "end_line": 60,
        "content": "new implementation", "truncated": False,
    })
    # An immutable Artifact may describe the same file, but it is not a live read.
    _observe(state, "read_artifact", {
        "artifact_id": "artifact_patch", "path": edit_path, "content": "frozen old patch",
    })
    for _ in range(8):
        _observe(state, "list_files", {"paths": ["train.py"]})
    before = state.model_dump_json()
    reads = _reads(state)
    old, other, new = reads["file_snippets"]
    assert [s["observed_at"] for s in (old, other, new)] == [1, 2, 4]
    assert old["content"] == "old implementation"
    assert old["modified_after_read_at"] == 3
    assert old["truncated"] is False
    assert "modified_after_read_at" not in other
    assert new["content"] == "new implementation"
    assert "modified_after_read_at" not in new
    assert "modified_after_read_at" not in reads["artifact_snippets"][0]
    assert state.model_dump_json() == before
    # Restarting requires no second cache or version state to recover the labels.
    assert _reads(AgentState.model_validate_json(before)) == reads


@pytest.mark.parametrize("tool,ok,path,event_type", [
    ("replace_text", False, "train.py", "observation"),
    ("replace_text", True, "other.py", "observation"),
    ("replace_text", True, "train.py", "action"),
    ("run_verification", True, "train.py", "observation"),
])
def test_only_successful_recorded_writes_mark_earlier_reads(tool, ok, path, event_type):
    state = _state()
    _observe(state, "read_file", {"path": "train.py", "content": "old"})
    _observe(state, tool, {"path": path}, ok=ok)
    state.events[-1].type = event_type
    assert "modified_after_read_at" not in _reads(state)["file_snippets"][0]


def test_marker_uses_latest_successful_write_without_invalidating_new_reads():
    state = _state()
    _observe(state, "read_file", {"path": "train.py", "start_line": 1, "content": "old"})
    _observe(state, "replace_text", {"path": "train.py"})
    _observe(state, "read_file", {"path": "train.py", "start_line": 2, "content": "middle"})
    _observe(state, "replace_text", {"path": "train.py"})
    _observe(state, "read_file", {"path": "train.py", "start_line": 3, "content": "new"})
    _observe(state, "replace_text", {"path": "train.py"}, ok=False)
    snippets = _reads(state)["file_snippets"]
    assert [s.get("modified_after_read_at") for s in snippets] == [4, 4, None]
    assert [s["content"] for s in snippets] == ["old", "middle", "new"]


def test_context_explains_chronology_and_does_not_claim_disk_freshness():
    state = _state()
    _observe(state, "read_file", {"path": "train.py", "content": "observed once"})
    content = next(s.content for s in workspace_context(state) if s.name == "workspace_reads")
    assert "oldest first" in content
    assert "not proof of freshness" in content
    assert "context_truncated" in content
