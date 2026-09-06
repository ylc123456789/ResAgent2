"""Deterministic checks for shared application state across multiple Runs."""

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from resagent2_capabilities import (
    ArtifactReadError,
    RegisteredArtifactReader,
    ResourceLayout,
)
from resagent2_contracts import (
    AgentOwner,
    ArtifactCandidate,
    ArtifactRef,
    Capability,
    CapabilityRegistry,
    CodeUnderstandInput,
    ExperimentRunInput,
    ModuleStatus,
    ModuleTaskRequest,
    ResearchRequest,
    RunBudget,
    RunStatus,
    ScientificTurnRequest,
    SessionId,
    TaskBudget,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSourceKind,
    scientific_session_id,
    task_session_id,
)
from resagent2_coding import NativeCodingAgent
from resagent2_experiment import NativeExperimentAgent
from resagent2_orchestrator import (
    ArtifactRegistry, JsonRunStore, ResearchController, ResearchRun, WorkflowScheduler,
)
from resagent2_runtime import JsonSessionStore, ScriptedLLMClient
from resagent2_scientific import ScientificAgent


def _research():
    return ResearchRequest(
        goal="Check Run isolation",
        budget=RunBudget(
            max_tasks=2, max_attempts_per_task=1, max_llm_calls=10,
            timeout_seconds=60,
        ),
    )


def test_task_session_identity_is_bounded_and_unambiguous():
    identities = [
        ("run_a", "task_shared", 1),
        ("run_b", "task_shared", 1),
        ("run_a", "task_shared", 2),
        ("run_a_task_b", "task_c", 1),
        ("run_a", "task_b_task_c", 1),
        ("run_" + "a" * 128, "task_" + "b" * 128, 1),
        ("run_" + "a" * 127 + "b", "task_" + "b" * 128, 1),
    ]
    names = [task_session_id(*identity) for identity in identities]
    assert len(set(names)) == len(identities)
    for identity, name in zip(identities, names):
        assert name == task_session_id(*identity)
        assert TypeAdapter(SessionId).validate_python(name) == name
        assert len(name) <= 136


def test_long_run_id_uses_one_valid_scientific_session_through_controller(tmp_path):
    run_id = "run_" + "a" * 128
    session_id = scientific_session_id(run_id)
    assert TypeAdapter(SessionId).validate_python(session_id) == session_id
    assert session_id != scientific_session_id("run_" + "a" * 127 + "b")
    assert scientific_session_id("run_short") == "session_scientific_run_short"

    class UnusedCompiler:
        def compile(self, *args, **kwargs):
            pytest.fail("direct conclusion must not compile a workflow")

    sessions = JsonSessionStore(tmp_path / "sessions")
    agent = ScientificAgent(ScriptedLLMClient([
        {"tool": "finish", "arguments": {
            "summary": "No experiment needed",
            "opinion": {"verdict": "inconclusive", "statement": "No evidence requested"},
        }},
    ]), store=sessions)
    scheduler = WorkflowScheduler(
        bindings={}, store=JsonRunStore(tmp_path / "runs"),
        artifact_root=tmp_path / "artifacts",
    )
    controller = ResearchController(
        scientific_port=agent, compiler=UnusedCompiler(), scheduler=scheduler,
        registry=CapabilityRegistry(definitions=[]),
    )
    run = controller.create_run(run_id, _research())
    assert run.status == RunStatus.COMPLETED
    assert run.scientific_session.id == session_id
    assert sessions.load(session_id).run_id == run_id


@pytest.mark.parametrize("agent_type", [NativeCodingAgent, NativeExperimentAgent])
def test_native_agents_share_store_without_cross_run_session_collision(
    tmp_path, monkeypatch, agent_type
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com",
         "commit", "--allow-empty", "-qm", "baseline"],
        cwd=workspace, check=True,
    )
    monkeypatch.setattr("resagent2_experiment.agent.HardwareAudit.text", lambda _: "test")
    ask = {
        "tool": "ask_user",
        "arguments": {
            "text": "Which option?", "reason": "User decision required",
            "requested_fields": ["option"],
        },
    }
    store = JsonSessionStore(tmp_path / "sessions")
    agent = agent_type(
        ScriptedLLMClient([ask, ask, ask]), store=store,
        resource_layout=ResourceLayout(resource_root=tmp_path / "resources"),
    )
    coding = agent_type is NativeCodingAgent
    request = ModuleTaskRequest(
        run_id="run_a", task_id="task_shared", attempt_number=1,
        capability=Capability.CODE_UNDERSTAND if coding else Capability.EXPERIMENT_RUN,
        goal="Ask for a decision",
        inputs=CodeUnderstandInput(question="Ask first") if coding else
            ExperimentRunInput(instructions="Ask first"),
        budget=TaskBudget(max_steps=5, max_llm_calls=5, timeout_seconds=30),
        workspace=WorkspaceGrant(
            root=str(workspace), mode=WorkspaceMode.READ_WRITE,
            allowed_paths=["."], source=WorkspaceSourceKind.LOCAL,
        ),
        workspace_id="ws_test", output_dir=str(tmp_path / "output"),
    )
    first = agent.invoke(request)
    second = agent.invoke(request.model_copy(update={"run_id": "run_b"}))
    assert first.status == second.status == ModuleStatus.NEEDS_USER_INPUT
    assert first.session.id != second.session.id
    assert store.load(first.session.id).run_id == "run_a"
    assert store.load(second.session.id).run_id == "run_b"

    # A normal pause/resume retains the original Session and Attempt.
    resumed = agent.invoke(request.model_copy(update={"parent_session_id": first.session.id}))
    assert resumed.status == ModuleStatus.NEEDS_USER_INPUT
    assert resumed.session.id == first.session.id
    assert store.load(first.session.id).attempt_number == 1
    assert len(list((tmp_path / "sessions").glob("*.json"))) == 2


def _artifact(tmp_path):
    path = tmp_path / "private.txt"
    content = b"private evidence"
    path.write_bytes(content)
    return ArtifactRef(
        id="artifact_private", kind="literature_search", producer=AgentOwner.SCIENTIFIC,
        run_id="run_a", session_id="session_a", uri=path.as_uri(),
        sha256=hashlib.sha256(content).hexdigest(), media_type="text/plain",
        summary="private evidence",
    )


@pytest.mark.parametrize("live", [False, True])
def test_reader_rejects_other_run_before_reading_bytes(tmp_path, monkeypatch, live):
    artifact = _artifact(tmp_path)
    reader = RegisteredArtifactReader(
        [] if live else [artifact], run_id="run_b",
        resolve=(lambda _: artifact) if live else None,
    )
    def forbidden_read(_path):
        pytest.fail("a foreign Run artifact must be rejected before file I/O")
    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    with pytest.raises(ArtifactReadError, match="unknown artifact"):
        reader.read_text(artifact.id)


def test_reader_rejects_resolver_returning_a_different_id(tmp_path, monkeypatch):
    artifact = _artifact(tmp_path)
    reader = RegisteredArtifactReader([], run_id="run_a", resolve=lambda _: artifact)
    monkeypatch.setattr(Path, "read_bytes", lambda _: pytest.fail("must not read"))
    with pytest.raises(ArtifactReadError, match="unknown artifact"):
        reader.read_text("artifact_not_granted")


def test_scientific_does_not_observe_another_runs_live_artifact(tmp_path, monkeypatch):
    artifact = _artifact(tmp_path)

    class WrongResolver:
        def resolve(self, artifact_id, *, run_id):
            assert run_id == "run_b"
            return artifact  # Even a misbehaving injected resolver cannot bypass the reader.

    client = ScriptedLLMClient([
        {"tool": "read_artifact", "arguments": {"artifact_id": artifact.id}},
        {"tool": "finish", "arguments": {
            "summary": "No authorized evidence available",
            "opinion": {"verdict": "inconclusive", "statement": "Evidence unavailable"},
        }},
    ])
    monkeypatch.setattr(Path, "read_bytes", lambda _: pytest.fail("must not read"))
    agent = ScientificAgent(client, registration_port=WrongResolver())
    result = agent.run(ScientificTurnRequest(
        run_id="run_b", research=_research(),
        budget=TaskBudget(max_steps=3, max_llm_calls=3, timeout_seconds=30),
    ))
    assert result.status == "completed"
    assert result.observed_artifact_ids == []
    assert "private evidence" not in client.contexts[-1].text


@pytest.fixture(params=["cli", "e2e"])
def registration(tmp_path, request):
    if request.param == "cli":
        from resagent2_cli.composition import _ScientificArtifactRegistration
    else:
        from e2e.real_e2e import _ScientificArtifactRegistration
    store = JsonRunStore(tmp_path / "runs")
    now = datetime.now(UTC)
    for run_id in ("run_a", "run_b"):
        store.save(ResearchRun(
            run_id=run_id, request=_research(), status=RunStatus.RUNNING,
            created_at=now, updated_at=now,
        ))
    return _ScientificArtifactRegistration(ArtifactRegistry(tmp_path / "artifacts"), store)


def test_live_registration_is_scoped_even_when_content_ids_match(registration):
    candidate = ArtifactCandidate(
        kind="literature_search", path="literature.json", media_type="application/json",
        summary="shared query", metadata={"papers": []},
    )
    first = registration.register_scientific(candidate, run_id="run_a", session_id="session_a")
    assert registration.resolve(first.id, run_id="run_b") is None
    second = registration.register_scientific(candidate, run_id="run_b", session_id="session_b")
    assert first.id == second.id
    assert registration.resolve(first.id, run_id="run_a") == first
    assert registration.resolve(second.id, run_id="run_b") == second


def test_scientific_reads_new_literature_in_the_same_turn(registration):
    class Backend:
        def search(self, query, **kwargs):
            return []

    class Client:
        step = 0
        artifact_id = None

        def next_action(self, context, action_type):
            self.step += 1
            if self.step == 1:
                return {"tool": "literature_search", "arguments": {"query": "scope test"}}
            if self.step == 2:
                self.artifact_id = next(iter(registration._store.load("run_a").artifacts))
                return {"tool": "read_artifact", "arguments": {"artifact_id": self.artifact_id}}
            assert "workspace_reads" in context.included_sections
            assert self.artifact_id in context.text
            assert '\\"papers\\": []' in context.text
            return {"tool": "finish", "arguments": {
                "summary": "No papers found",
                "opinion": {"verdict": "inconclusive", "statement": "No papers found",
                            "evidence_artifact_ids": [self.artifact_id]},
            }}

    client = Client()
    agent = ScientificAgent(client, literature_backend=Backend(), registration_port=registration)
    result = agent.run(ScientificTurnRequest(
        run_id="run_a", research=_research(),
        budget=TaskBudget(max_steps=5, max_llm_calls=5, timeout_seconds=30),
    ))
    assert result.status == "completed"
    state = agent.store.load(result.session.id)
    assert client.artifact_id in state.memory["read_artifact_ids"]
    reads = [e for e in state.events if e.type == "observation" and e.tool == "read_artifact"]
    assert reads[-1].data["value"]["content"] == '{"papers": []}'
    assert "read_artifact_summaries" not in state.memory
