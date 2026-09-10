"""Resource refresh uses existing pause/session machinery, without a provider."""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from resagent2_capabilities import DatasetCatalog, ResourceLayout
from resagent2_contracts import (
    Capability, CodeModifyInput, CodeUnderstandInput, ExperimentRunInput,
    ModuleTaskRequest, ResearchRequest, RunBudget, ScientificTurnRequest,
    TaskBudget, UserAnswer, WorkspaceGrant, WorkspaceMode, WorkspaceSourceKind,
    scientific_session_id, task_session_id,
)
from resagent2_coding import NativeCodingAgent
from resagent2_experiment import NativeExperimentAgent
from resagent2_runtime import JsonSessionStore, ScriptedLLMClient
from resagent2_scientific import ScientificAgent


def _ask(scientific=False):
    args = {
        "text": "Provision demo under the dataset root and register it, then confirm.",
        "requested_fields": ["dataset_ready"],
        "reason": "resource check",
    }
    if scientific:
        args["assessment"] = {"statement": "Check dataset availability"}
    return {"tool": "ask_user", "arguments": args}


def _probe(root, kind, phase):
    """A fresh OS process checks real native-Agent context and disk Session.

    Actions are scripted: this proves refreshed input and Session reuse, not
    that every model always follows the missing-resource prompt.
    """
    root = Path(root)
    layout = ResourceLayout(resource_root=root / "resources")
    refs = DatasetCatalog(layout.dataset_root).references()
    scientific = kind == "scientific"
    session_id = (
        scientific_session_id("run_resources") if scientific
        else task_session_id("run_resources", "task_resources", 1)
    )
    sessions = JsonSessionStore(root / "sessions")
    before = sessions.load(session_id) if phase else None
    client = ScriptedLLMClient([_ask(scientific)])
    answers = [] if phase == 0 else [UserAnswer(
        question_id=f"question_ready_{phase}", values={"dataset_ready": "yes"},
        answered_at=datetime.now(UTC),
    )]
    common = dict(
        run_id="run_resources", dataset_refs=refs, answers=answers,
        budget=TaskBudget(max_steps=10, max_llm_calls=10, timeout_seconds=30),
        parent_session_id=session_id if phase else None,
    )
    if scientific:
        request = ScientificTurnRequest(
            **common, research=ResearchRequest(
                goal="Check availability without downloading data",
                budget=RunBudget(
                    max_tasks=1, max_attempts_per_task=1,
                    max_llm_calls=20, timeout_seconds=60,
                ),
            ),
        )
        result = ScientificAgent(
            client, store=sessions, resource_layout=layout,
        ).run(request)
    else:
        capability = Capability(kind)
        inputs = {
            Capability.CODE_UNDERSTAND: CodeUnderstandInput(question="Inspect code"),
            Capability.CODE_MODIFY: CodeModifyInput(instructions="Inspect before editing"),
            Capability.EXPERIMENT_RUN: ExperimentRunInput(instructions="Inspect before running"),
        }[capability]
        request = ModuleTaskRequest(
            **common, task_id="task_resources", attempt_number=1,
            capability=capability, goal="Check resources", inputs=inputs,
            workspace=WorkspaceGrant(
                root=str(root / "repo"), mode=(
                    WorkspaceMode.READ_ONLY if capability == Capability.CODE_UNDERSTAND
                    else WorkspaceMode.READ_WRITE
                ), allowed_paths=["."], source=WorkspaceSourceKind.LOCAL,
            ),
            workspace_id="ws_resources", output_dir=str(root / "output"),
        )
        agent_type = NativeExperimentAgent if kind == "experiment_run" else NativeCodingAgent
        result = agent_type(client, store=sessions, resource_layout=layout).invoke(request)

    assert result.status == "needs_user_input", result
    assert result.session.id == session_id
    text = client.contexts[0].text
    expected = '["demo"]' if phase == 2 else "[]"
    assert f'"available_dataset_ids": {expected}' in text
    missing = '["demo"]' if phase == 1 else "[]"
    assert f'"unavailable_dataset_ids": {missing}' in text
    after = sessions.load(session_id)
    assert after.attempt_number == (None if scientific else 1)
    if before is not None:
        assert after.created_at == before.created_at
        assert len(after.events) > len(before.events)
        if "workspace_snapshot" in before.memory:
            assert after.memory["workspace_snapshot"] == before.memory["workspace_snapshot"]


@pytest.mark.parametrize("kind", [
    "scientific", "code_understand", "code_modify", "experiment_run",
])
def test_native_agents_recheck_resources_across_processes(tmp_path, kind):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "util.py").write_text("VALUE = 1\n", encoding="utf-8")
    for args in [
        ["init", "-q"], ["add", "."],
        ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "baseline"],
    ]:
        subprocess.run(["git", *args], cwd=repo, check=True)
    dataset_root = tmp_path / "resources" / "datasets"
    dataset_root.mkdir(parents=True)

    for phase in range(3):
        if phase == 1:
            # A registration and a verbal confirmation do not create the data.
            (dataset_root / "catalog.json").write_text('{"demo": "demo"}', encoding="utf-8")
        if phase == 2:
            (dataset_root / "demo").mkdir()
        code = (
            "import runpy; "
            f"ns = runpy.run_path({str(Path(__file__).resolve())!r}); "
            f"ns['_probe']({str(tmp_path)!r}, {kind!r}, {phase})"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], text=True, capture_output=True, timeout=30,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(list((tmp_path / "sessions").glob("*.json"))) == 1


def test_scientific_invalid_resource_is_a_controlled_failure(tmp_path):
    from resagent2_contracts import DatasetRef

    layout = ResourceLayout(resource_root=tmp_path)
    layout.dataset_root.mkdir()
    (layout.dataset_root / "link").symlink_to(tmp_path / "outside", target_is_directory=True)
    client = ScriptedLLMClient([])
    result = ScientificAgent(client, resource_layout=layout).run(ScientificTurnRequest(
        run_id="run_invalid", dataset_refs=[DatasetRef(dataset_id="x", relative_path="link")],
        research=ResearchRequest(
            goal="No unsafe resource paths", budget=RunBudget(
                max_tasks=1, max_attempts_per_task=1, max_llm_calls=5, timeout_seconds=60,
            ),
        ),
        budget=TaskBudget(max_steps=5, max_llm_calls=5, timeout_seconds=30),
    ))
    assert result.status == "failed"
    assert result.error.code == "invalid_input"
    assert not client.contexts

