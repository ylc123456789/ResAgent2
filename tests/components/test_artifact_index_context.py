"""All Agents keep a compact complete directory separate from source snippets."""

import hashlib
import json
from datetime import UTC, datetime

import pytest

from resagent2_components import artifact_index_context, merge_artifact_index
from resagent2_contracts import AgentOwner, AgentPermissions, AgentRequest, ArtifactRef, TaskBudget
from resagent2_runtime import (
    AgentEvent, AgentState, ContextComposer, ContextMaterial, ContextSection,
    JsonSessionStore,
)
from resagent2_runtime.context import ContextBudgetExceeded
from resagent2_coding.context import build_context as coding_context
from resagent2_experiment.context import build_context as experiment_context
from resagent2_scientific.context import build_context as scientific_context


BUILDERS = [
    (AgentOwner.CODING, coding_context),
    (AgentOwner.EXPERIMENT, experiment_context),
    (AgentOwner.SCIENTIFIC, scientific_context),
]
NOW = datetime(2026, 10, 7, tzinfo=UTC)
BODY = "BODY_ONLY_SECRET_DETAIL " * 4000


def artifact(root, name):
    path = root / f"{name}.txt"
    path.write_text(BODY, encoding="utf-8")
    return ArtifactRef(
        id=f"artifact_{name}", kind="data", run_id="run_directory",
        producer=AgentOwner.ORCHESTRATOR, uri=path.as_uri(),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        media_type="text/plain", summary=f"Saved {name}",
        metadata={"source_type": "import"},
    )


def invocation(owner, refs):
    scope = {} if owner == AgentOwner.SCIENTIFIC else {
        "task_id": "task_directory", "attempt_number": 1,
    }
    return AgentRequest(
        run_id="run_directory", agent=owner, instruction="Review saved results",
        input_artifacts=refs, permissions=AgentPermissions(), budget=TaskBudget(max_llm_calls=3, timeout_seconds=30),
        **scope,
    )


def state(request, entries):
    return AgentState(
        session_id="session_directory", agent_name=request.agent.value,
        owner=request.agent, run_id=request.run_id,
        task_id=request.task_id, attempt_number=request.attempt_number,
        created_at=NOW, updated_at=NOW, memory={"artifact_index": entries},
    )


@pytest.mark.parametrize("owner,builder", BUILDERS)
def test_every_builder_presents_one_compact_complete_directory(tmp_path, owner, builder):
    supplied = artifact(tmp_path, "supplied")
    output = artifact(tmp_path, "output")
    request = invocation(owner, [supplied])
    current = state(request, merge_artifact_index([], [supplied, output, output]))
    before = current.model_dump_json()
    sections = builder(request, current)
    indexes = [section for section in sections if section.name == "artifact_index"]
    assert len(indexes) == 1
    index = indexes[0]
    assert index.required
    entries = json.loads(index.content)["artifacts"]
    assert [entry["artifact_id"] for entry in entries] == [supplied.id, output.id]
    assert {entry["summary"] for entry in entries} == {supplied.summary, output.summary}
    for ref in (supplied, output):
        assert ref.uri not in index.content
        assert ref.sha256 not in index.content
    assert "BODY_ONLY_SECRET_DETAIL" not in index.content
    assert current.model_dump_json() == before
    assert current.events == []


@pytest.mark.parametrize("owner,builder", BUILDERS)
def test_budget_curbs_body_without_slicing_directory(tmp_path, owner, builder):
    refs = [artifact(tmp_path, f"item_{number}") for number in range(25)]
    request = invocation(owner, refs[:10])
    current = state(request, merge_artifact_index([], refs[10:]))
    current.events.append(AgentEvent(
        sequence=1, step=1, type="observation", tool="read_artifact",
        data={"ok": True, "value": {
            "artifact_id": refs[0].id, "content": BODY,
            "start_line": 1, "end_line": 4000, "truncated": False,
        }}, created_at=NOW,
    ))
    before = current.model_dump_json()
    sections = builder(request, current)
    composer = ContextComposer()
    roomy = composer.compose("Agent rules", sections, max_tokens=100_000)
    assert BODY in roomy.text
    frames = [
        ContextSection(name=item.name, content=item.render(0), required=item.required)
        if isinstance(item, ContextMaterial) else item
        for item in sections
    ]
    base = composer.compose("Agent rules", frames, max_tokens=100_000)
    budget = base.estimated_tokens + 20
    context = composer.compose("Agent rules", sections, max_tokens=budget)
    index = next(item for item in sections if item.name == "artifact_index")
    assert index.content in context.text
    assert context.included_sections.count("artifact_index") == 1
    assert context.estimated_tokens <= budget
    assert BODY not in context.text
    assert all(ref.id in index.content for ref in refs)
    assert current.model_dump_json() == before
    required_index = composer.compose("Agent rules", [index], max_tokens=100_000)
    with pytest.raises(ContextBudgetExceeded, match="artifact_index"):
        composer.compose("Agent rules", [index], max_tokens=required_index.estimated_tokens - 1)


def test_session_restart_keeps_outputs_and_merges_new_inputs_once(tmp_path):
    first = artifact(tmp_path, "first")
    output = artifact(tmp_path, "tool_output")
    new_input = artifact(tmp_path, "new_input")
    request = invocation(AgentOwner.CODING, [first])
    current = state(request, merge_artifact_index([], [first, output]))
    store = JsonSessionStore(tmp_path / "sessions")
    store.save(current)
    restored = JsonSessionStore(tmp_path / "sessions").load(current.session_id)
    restored.memory["artifact_index"] = merge_artifact_index(
        restored.memory["artifact_index"], [output, new_input, new_input],
    )
    store.save(restored)
    restarted = JsonSessionStore(tmp_path / "sessions").load(current.session_id)
    before = restarted.model_dump_json()
    directory = artifact_index_context(invocation(AgentOwner.CODING, [first]), restarted)
    assert [entry["artifact_id"] for entry in json.loads(directory.content)["artifacts"]] == [
        first.id, output.id, new_input.id,
    ]
    assert restarted.model_dump_json() == before
