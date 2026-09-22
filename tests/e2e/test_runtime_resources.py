"""Resource refresh uses artifact inputs and persisted Sessions across processes."""

from resagent2_contracts import AgentPermissions

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from resagent2_components import DatasetCatalog, ResourceLayout, dataset_context, resolve_dataset_refs
from resagent2_contracts import (
    AgentOwner, AgentRequest, ArtifactCandidate, TaskBudget, RecordedAnswer,
    WorkspaceGrant, WorkspaceAccess, WorkspaceSourceKind, scientific_session_id, task_session_id,
)
from resagent2_coding import NativeCodingAgent
from resagent2_experiment import NativeExperimentAgent
from resagent2_orchestrator import ArtifactRegistry
from resagent2_runtime import JsonSessionStore, ScriptedLLMClient
from resagent2_scientific import ScientificAgent


def _ask(scientific=False):
    args = {"text": "Provision demo under the dataset root and register it, then confirm.",
            "requested_fields": ["dataset_ready"]}
    if scientific:
        args["assessment"] = {"statement": "Check dataset availability"}
    return {"tool": "ask_user", "arguments": args}


def _system_artifact(registry, kind, content, *, run_id="run_resources", session_id=None, scientific=False):
    candidate = ArtifactCandidate(
        kind=kind, path=kind + ".json", media_type="application/json", summary=kind,
        content=json.dumps(content),
    )
    kwargs = {}
    if kind == "answer":
        kwargs = {"session_id": session_id} if scientific else {"task_id": "task_resources", "attempt_number": 1}
    return registry.register_system_artifact(
        candidate, run_id=run_id,
        source_type="dataset_catalog" if kind == "dataset_catalog" else "controller_answer",
        **kwargs,
    )


def _probe(root, kind, phase):
    root = Path(root)
    layout = ResourceLayout(resource_root=root / "resources")
    datasets = DatasetCatalog(layout.dataset_root).references()
    scientific = kind == "scientific"
    session_id = scientific_session_id("run_resources") if scientific else task_session_id("run_resources", "task_resources", 1)
    sessions = JsonSessionStore(root / "sessions")
    before = sessions.load(session_id) if phase else None
    actions = [_ask(scientific)]
    if phase and not scientific:
        actions.insert(0, {"tool": "read_file", "arguments": {"path": "util.py"}})
    client = ScriptedLLMClient(actions)
    registry = ArtifactRegistry(root / "artifacts")
    refs = [_system_artifact(registry, "dataset_catalog", {"datasets": [ref.model_dump(mode="json") for ref in datasets]})]
    resume = []
    if phase:
        answer = RecordedAnswer(
            question_id=f"question_ready_{phase}", question_text=_ask(scientific)["arguments"]["text"],
            requested_fields=["dataset_ready"], values={"dataset_ready": f"yes, reply_phase_{phase}"},
            answered_at=datetime.now(UTC), run_id="run_resources",
            **({"session_id": session_id} if scientific else {"task_id": "task_resources", "attempt_number": 1}),
        )
        ref = _system_artifact(registry, "answer", answer.model_dump(mode="json"), session_id=session_id, scientific=scientific)
        refs.append(ref)
        resume = [ref.id]
    common = dict(
        run_id="run_resources", agent=AgentOwner(kind), input_artifacts=refs,
        resume_artifact_ids=resume, parent_session_id=session_id if phase else None,
        budget=TaskBudget(max_llm_calls=10, timeout_seconds=30),
    )
    if scientific:
        request = AgentRequest(**common, instruction='Check availability without downloading data', permissions=AgentPermissions(execute_commands=True, prepare_environment=True))
        agent = ScientificAgent(client, store=sessions, resource_layout=layout)
    else:
        request = AgentRequest(**common, task_id='task_resources', attempt_number=1, instruction='Inspect the provided resources', workspace=WorkspaceGrant(root=str(root / 'repo'), source=WorkspaceSourceKind.LOCAL, access=WorkspaceAccess(read_paths=['.'], write_paths=[])), workspace_id='ws_resources', output_dir=str(root / 'output'), permissions=AgentPermissions(execute_commands=True, prepare_environment=True))
        cls = NativeExperimentAgent if kind == "experiment" else NativeCodingAgent
        agent = cls(client, store=sessions, resource_layout=layout)
    result = agent.invoke(request)
    assert result.status == "needs_user_input", result
    assert result.session.id == session_id
    text = client.contexts[0].text
    expected = '["demo"]' if phase == 2 else "[]"
    assert f'"available_dataset_ids": {expected}' in text
    missing = '["demo"]' if phase == 1 else "[]"
    assert f'"unavailable_dataset_ids": {missing}' in text
    shared = dataset_context(resolve_dataset_refs(layout.dataset_root, datasets))
    assert shared["availability_basis"] in text
    assert shared["missing_dataset_guidance"] in text
    for context in client.contexts:
        if phase:
            assert f"reply_phase_{phase}" in context.text
            assert f"reply_phase_{phase - 1}" not in context.text
            assert sum(name.startswith("material_") for name in context.included_sections) == 1
    after = sessions.load(session_id)
    assert after.attempt_number == (None if scientific else 1)
    if before:
        assert after.created_at == before.created_at
        assert len(after.events) > len(before.events)
        if "workspace_snapshot" in before.memory:
            assert after.memory["workspace_snapshot"] == before.memory["workspace_snapshot"]


@pytest.mark.parametrize("kind", ["scientific", "coding", "experiment"])
def test_native_agents_recheck_resources_across_processes(tmp_path, kind):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "util.py").write_text("VALUE = 1\n")
    for args in [["init", "-q"], ["add", "."],
                 ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "baseline"]]:
        subprocess.run(["git", *args], cwd=repo, check=True)
    dataset_root = tmp_path / "resources" / "datasets"
    dataset_root.mkdir(parents=True)
    for phase in range(3):
        if phase == 1:
            (dataset_root / "catalog.json").write_text('{"demo": "demo"}')
        if phase == 2:
            (dataset_root / "demo").mkdir()
        code = ("import runpy; " + f"ns = runpy.run_path({str(Path(__file__).resolve())!r}); "
                + f"ns['_probe']({str(tmp_path)!r}, {kind!r}, {phase})")
        proc = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True, timeout=30)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(list((tmp_path / "sessions").glob("*.json"))) == 1


def test_scientific_invalid_resource_is_controlled_failure(tmp_path):
    layout = ResourceLayout(resource_root=tmp_path)
    layout.dataset_root.mkdir()
    (layout.dataset_root / "link").symlink_to(tmp_path / "outside", target_is_directory=True)
    ref = _system_artifact(
        ArtifactRegistry(tmp_path / "artifacts"), "dataset_catalog",
        {"datasets": [{"dataset_id": "x", "relative_path": "link"}]}, run_id="run_invalid",
    )
    client = ScriptedLLMClient([])
    result = ScientificAgent(client, resource_layout=layout).invoke(AgentRequest(run_id='run_invalid', agent=AgentOwner.SCIENTIFIC, input_artifacts=[ref], instruction='No unsafe resource paths', budget=TaskBudget(max_llm_calls=5, timeout_seconds=30), permissions=AgentPermissions(execute_commands=True, prepare_environment=True)))
    assert result.status == "failed"
    assert result.error.code == "invalid_input"
    assert not client.contexts
