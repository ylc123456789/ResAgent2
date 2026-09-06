"""Exercise native Agent assembly and real Runtime composition at read-pool capacity."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime

import pytest

from resagent2_capabilities import (
    GitWorkspace, HardwareAudit, ResourceLayout, WorkspaceBoundary, WorkspaceSnapshot,
)
from resagent2_coding import NativeCodingAgent
from resagent2_contracts import (
    AgentOwner, ArtifactRef, Capability, CodeModifyInput, ErrorCode,
    ExperimentRunInput, ModuleStatus, ModuleTaskRequest, SessionStatus,
    TaskBudget, WorkspaceGrant, WorkspaceMode, WorkspaceSourceKind, task_session_id,
)
from resagent2_experiment import NativeExperimentAgent
from resagent2_runtime import (
    AgentEvent, AgentState, ContextComposer, InMemorySessionStore, ToolObservation,
)


def _body(label: str) -> str:
    prefix, suffix = f"{label}_BEGIN\n", f"\n{label}_END"
    return prefix + "x" * (6000 - len(prefix) - len(suffix)) + suffix


FILE_BODY = _body("FILE")
ARTIFACT_BODY = _body("ARTIFACT")


class _CaptureClient:
    def __init__(self):
        self.contexts = []

    def next_action(self, context, action_type):
        self.contexts.append(context)
        # Pause after observing the prompt; no environment installation, code
        # execution, or fabricated success is needed for this composition test.
        return {
            "tool": "ask_user",
            "arguments": {
                "text": "Confirm the next evaluation?",
                "requested_fields": ["approval"],
                "reason": "This fixture ends at the context boundary",
            },
        }


def _native_with_full_read_history(tmp_path, monkeypatch, capability, *, max_tokens=None, artifact_first=False):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "train.py").write_text(FILE_BODY, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "add", "train.py"], cwd=root, check=True)
    subprocess.run([
        "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "-qm", "context fixture",
    ], cwd=root, check=True)
    frozen = tmp_path / "artifact.patch"
    frozen.write_text(ARTIFACT_BODY, encoding="utf-8")
    artifact = ArtifactRef(
        id="artifact_context", kind="code_patch", producer=AgentOwner.CODING,
        run_id="run_capacity", task_id="task_prior", attempt_number=1,
        uri=frozen.as_uri(), sha256=hashlib.sha256(frozen.read_bytes()).hexdigest(),
        media_type="text/plain", summary="Previous code patch",
    )
    grant = WorkspaceGrant(
        root=str(root), mode=WorkspaceMode.READ_WRITE,
        allowed_paths=["."], source=WorkspaceSourceKind.LOCAL,
    )
    baseline = GitWorkspace(WorkspaceBoundary(grant)).snapshot()
    coding = capability == Capability.CODE_MODIFY
    session_id = task_session_id("run_capacity", "task_capacity", 1)
    request = ModuleTaskRequest(
        run_id="run_capacity", task_id="task_capacity", attempt_number=1,
        parent_session_id=session_id, capability=capability,
        goal="Use the current source and the previous patch to determine the next step",
        inputs=CodeModifyInput(instructions="Review source and patch before modifying") if coding
        else ExperimentRunInput(instructions="Review source and patch before evaluating"),
        input_artifacts=[artifact], workspace=grant, workspace_id="ws_capacity",
        output_dir=str(tmp_path / "outputs"),
        budget=TaskBudget(max_steps=2, max_llm_calls=2, timeout_seconds=30),
    )
    now = datetime.now(UTC)
    state = AgentState(
        session_id=session_id, agent_name="coding-modify" if coding else "experiment-run",
        owner=AgentOwner.CODING if coding else AgentOwner.EXPERIMENT,
        run_id=request.run_id, task_id=request.task_id, attempt_number=1,
        status=SessionStatus.PAUSED, created_at=now, updated_at=now,
        memory={
            "workspace_snapshot": WorkspaceSnapshot(tree_hash=baseline.tree_hash).to_memory(),
            "read_paths": ["train.py"], "read_artifact_ids": [artifact.id],
            "hardware": "OS: test\nCPU cores: 4\nGPU: none visible",
            "repo": {"repo_url": str(root), "commit": "fixture"},
            "edit_revision": 0,
        },
    )
    read_observations = [
        ("read_file", {"path": "train.py", "start_line": 1, "end_line": 3,
                       "content": FILE_BODY, "truncated": False}),
        ("read_artifact", {"artifact_id": artifact.id, "start_line": 1, "end_line": 3,
                           "content": ARTIFACT_BODY, "truncated": False}),
    ]
    if artifact_first:
        read_observations.reverse()
    # Old reads must survive a populated recent-observation window. Long list
    # values also exercise Runtime's real per-observation trimming and listing.
    observations = [*read_observations, *[
        ("list_files", {"path": ".", "paths": [f"package/module_{i:02d}.py" for i in range(80)],
                        "truncated": False})
        for _ in range(8)
    ]]
    for index, (tool, value) in enumerate(observations, start=1):
        state.events.append(AgentEvent(
            sequence=index, step=index, type="observation", tool=tool,
            data={"ok": True, "summary": "Observed " + "entry " * 30, "value": value},
            created_at=now,
        ))
    state.runtime_feedback = ToolObservation(
        ok=False, summary="Resolve the previous rejection before trying to finish",
        value={"reason": "RECOVERABLE_FEEDBACK " + "details " * 180},
    )
    state.runtime_feedback_source = "completion_check"
    state.step = len(state.events)
    store = InMemorySessionStore()
    store.save(state)
    client = _CaptureClient()
    # Deterministic hardware text only; real AgentDefinition, tools, prompts,
    # context builder, permission policy and AgentLoop remain unmodified.
    monkeypatch.setattr(HardwareAudit, "text", lambda self: "CPU test host")
    options = {} if max_tokens is None else {"max_context_tokens": max_tokens}
    agent_class = NativeCodingAgent if coding else NativeExperimentAgent
    agent = agent_class(
        client, store=store, resource_layout=ResourceLayout(resource_root=tmp_path / "resources"),
        **options,
    )
    return agent, client, request


@pytest.mark.parametrize("capability", [Capability.CODE_MODIFY, Capability.EXPERIMENT_RUN])
@pytest.mark.parametrize("artifact_first", [False, True])
def test_native_default_context_keeps_both_full_read_pools(tmp_path, monkeypatch, capability, artifact_first):
    agent, client, request = _native_with_full_read_history(
        tmp_path, monkeypatch, capability, artifact_first=artifact_first,
    )
    assert agent.max_context_tokens == 8192
    result = agent.invoke(request)
    assert result.status == ModuleStatus.NEEDS_USER_INPUT, result.model_dump(mode="json")
    assert len(client.contexts) == 1
    context = client.contexts[0]
    assert {"workspace_reads", "environment", "tool_contracts", "recent_observations", "runtime_feedback"} <= set(context.included_sections)
    assert context.estimated_tokens <= 8192
    assert ContextComposer.estimate_tokens(context.text) <= 8192
    assert "read_file: path" in context.text
    assert "read_artifact: artifact_id" in context.text
    assert "RECOVERABLE_FEEDBACK" in context.text
    # Parse the actual client input, not the context-builder's intermediate
    # sections: runtime contracts/history/feedback and budgeting ran already.
    section = context.text.split("## workspace_reads\n", 1)[1].split("\n\n## ", 1)[0]
    reads = json.loads(section.split("\n", 1)[1])
    assert sum(len(item["content"]) for item in reads["file_snippets"]) == 6000
    assert sum(len(item["content"]) for item in reads["artifact_snippets"]) == 6000
    assert reads["file_snippets"][0]["content"] == FILE_BODY
    assert reads["artifact_snippets"][0]["content"] == ARTIFACT_BODY
    assert not reads["file_snippets"][0]["truncated"]
    assert not reads["artifact_snippets"][0]["truncated"]


@pytest.mark.parametrize("capability", [Capability.CODE_MODIFY, Capability.EXPERIMENT_RUN])
@pytest.mark.parametrize("explicit_limit", [1024, 4096])
def test_native_explicit_small_context_limit_is_not_silently_expanded(tmp_path, monkeypatch, capability, explicit_limit):
    agent, client, request = _native_with_full_read_history(
        tmp_path, monkeypatch, capability, max_tokens=explicit_limit,
    )
    assert agent.max_context_tokens == explicit_limit
    result = agent.invoke(request)
    assert result.status == ModuleStatus.FAILED, result.model_dump(mode="json")
    assert result.error.code == ErrorCode.BUDGET_EXHAUSTED
    assert result.error.retryable is False
    assert result.llm_calls == 0
    assert client.contexts == []
