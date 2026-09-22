"""Deletion must be reachable through Coding's actual native invocation path."""

from datetime import UTC, datetime
import json
import subprocess
from typing import get_args

import httpx
import pytest

from resagent2_coding import CodingAction, NativeCodingAgent
from resagent2_contracts import (
    AgentPermissions, AgentRequest, ArtifactCandidate, ModuleStatus, QuestionDraft,
    RecordedAnswer, TaskBudget, WorkspaceAccess, WorkspaceGrant,
)
from resagent2_components import ResourceLayout
from resagent2_orchestrator.artifacts import ArtifactRegistry
from resagent2_runtime import JsonSessionStore, OpenAICompatibleClient


def setup(tmp_path, monkeypatch, actions):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "keep.txt").write_text("keep\n")
    replies = iter(actions)
    sent_tools = []
    sequence = 0
    monkeypatch.setenv("DELETE_TEST_KEY", "test-only")

    def send(request, **kwargs):
        nonlocal sequence
        sequence += 1
        body = json.loads(request.content)
        sent_tools.append({tool["function"]["name"] for tool in body["tools"]})
        tool, arguments = next(replies)
        return httpx.Response(200, request=request, json={"choices": [{
            "finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [{
                "id": f"call_{sequence}", "type": "function",
                "function": {"name": tool, "arguments": json.dumps(arguments)},
            }]},
        }]})

    monkeypatch.setattr("resagent2_runtime.llm.send_request", send)
    layout = ResourceLayout.from_env(data_root=tmp_path / "data")

    def agent():
        return NativeCodingAgent(
            OpenAICompatibleClient(
                model="delete-test", api_base="https://example.invalid/v1",
                api_key_env="DELETE_TEST_KEY",
            ),
            store=JsonSessionStore(tmp_path / "sessions"), resource_layout=layout,
        )

    request = AgentRequest(
        agent="coding", run_id="run_delete", task_id="task_delete", attempt_number=1,
        instruction="Delete the requested obsolete path; preserve keep.txt.",
        budget=TaskBudget(max_llm_calls=6, timeout_seconds=30),
        permissions=AgentPermissions(),
        workspace=WorkspaceGrant(
            root=str(repo), source="local",
            access=WorkspaceAccess(read_paths=["."], write_paths=["obsolete"]),
        ),
        output_dir=str(tmp_path / "out"),
    )
    return repo, request, agent, sent_tools


FINISH = ("finish", {"report": "Removed obsolete files; no commands executed."})


@pytest.mark.parametrize("directory", [False, True])
def test_native_coding_deletes_file_or_empty_directory_without_asking(
    tmp_path, monkeypatch, directory,
):
    repo, request, agent, sent = setup(tmp_path, monkeypatch, [
        ("delete_path", {"path": "obsolete"}), FINISH,
    ])
    target = repo / "obsolete"
    if directory:
        target.mkdir()
    else:
        target.write_text("obsolete\n")

    result = agent().invoke(request)

    assert result.status == ModuleStatus.COMPLETED
    assert not target.exists()
    assert (repo / "keep.txt").read_text() == "keep\n"
    assert result.control is None
    assert all(names == set(get_args(CodingAction.model_fields["tool"].annotation))
               for names in sent)
    if not directory:
        patch = next(item for item in result.artifacts if item.kind == "code_patch")
        assert "deleted file mode" in patch.content
        assert "-obsolete" in patch.content


def test_native_coding_recursive_delete_resumes_from_approved_snapshot(tmp_path, monkeypatch):
    deletion = ("delete_path", {"path": "obsolete", "recursive": True})
    repo, request, agent, _ = setup(tmp_path, monkeypatch, [deletion, deletion, FINISH])
    target = repo / "obsolete"
    target.mkdir()
    for name in ("first.txt", "second.txt"):
        (target / name).write_text(name)

    first = agent().invoke(request)
    assert first.status == ModuleStatus.NEEDS_USER_INPUT
    question = QuestionDraft.model_validate_json(first.artifacts[0].content)
    assert question.action.tool == "delete_path"
    assert {item["path"] for item in question.action.context["deletion"]["entries"]} == {
        "obsolete", "obsolete/first.txt", "obsolete/second.txt",
    }
    assert (target / "first.txt").read_text() == "first.txt"
    assert (target / "second.txt").read_text() == "second.txt"
    recorded = RecordedAnswer(
        question_id="question_delete", question_text=question.text,
        requested_fields=question.requested_fields, options=question.options,
        values={"approve": "yes"}, answered_at=datetime.now(UTC),
        run_id=request.run_id, task_id=request.task_id, attempt_number=1,
        action=question.action,
    )
    ref = ArtifactRegistry(tmp_path / "artifacts").register_system_artifact(
        ArtifactCandidate(
            kind="answer", path="answer.json", media_type="application/json",
            summary="Approve this deletion", content=recorded.model_dump_json(),
        ),
        run_id=request.run_id, task_id=request.task_id, attempt_number=1,
        source_type="controller_answer",
    )
    resumed = request.model_copy(update={
        "parent_session_id": first.session.id,
        "input_artifacts": [ref], "resume_artifact_ids": [ref.id],
    })

    second = agent().invoke(resumed)  # New Agent and store instance, same persisted Session.

    assert second.status == ModuleStatus.COMPLETED
    assert second.session.id == first.session.id
    assert not target.exists()
    assert (repo / "keep.txt").read_text() == "keep\n"
    state = JsonSessionStore(tmp_path / "sessions").load(second.session.id)
    assert state.pending_action is None
    assert state.memory["edit_revision"] == 1
    deletions = [event for event in state.events
                 if event.tool == "delete_path" and event.type == "observation"
                 and event.data.get("value")]
    assert len(deletions) == 1
    assert set(deletions[0].data["value"]["deleted_paths"]) == {
        "obsolete", "obsolete/first.txt", "obsolete/second.txt",
    }

