"""Text windows work across workspace and frozen evidence without weakening grants."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from resagent2_capabilities import (
    ArtifactReadError, ReadArtifactTool, ReadFileTool, RegisteredArtifactReader,
    SearchTextTool, WorkspaceBoundary, WorkspacePermissionError,
)
from resagent2_contracts import AgentOwner, ArtifactRef, WorkspaceGrant, WorkspaceMode
from resagent2_runtime import AgentState


def _state():
    now = datetime.now(UTC)
    return AgentState(
        session_id="session_reader", agent_name="reader", owner=AgentOwner.CODING,
        run_id="run_reader", task_id="task_reader", attempt_number=1,
        created_at=now, updated_at=now,
    )

def _boundary(root, **kwargs):
    return WorkspaceBoundary(WorkspaceGrant(
        root=str(root), mode=WorkspaceMode.READ_ONLY, source="local",
        allowed_paths=kwargs.get("allowed_paths", ["."]),
        denied_paths=kwargs.get("denied_paths", []),
    ))


def _artifact(path):
    return ArtifactRef(
        id="artifact_text", kind="text", producer=AgentOwner.EXPERIMENT,
        run_id="run_reader", task_id="task_reader", attempt_number=1,
        uri=path.as_uri(), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        media_type="text/plain", summary="Frozen evidence",
    )


@pytest.mark.parametrize("start,end,expected", [
    (None, None, "one\ntwo\nthree\nfour\n"),
    (2, 3, "two\nthree\n"),
    (3, None, "three\nfour\n"),
    (None, 2, "one\ntwo\n"),
    (8, 10, ""),
])
def test_workspace_and_artifact_share_line_range_semantics(tmp_path, start, end, expected):
    path = tmp_path / "text.txt"
    path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    ref = _artifact(path)
    file_tool = ReadFileTool(_boundary(tmp_path))
    artifact_tool = ReadArtifactTool(RegisteredArtifactReader([ref], run_id=ref.run_id))
    file_result = file_tool.execute(_state(), file_tool.input_model(
        path="text.txt", start_line=start, end_line=end,
    ))
    artifact_result = artifact_tool.execute(_state(), artifact_tool.input_model(
        artifact_id=ref.id, start_line=start, end_line=end,
    ))
    for result in (file_result, artifact_result):
        assert result.value["start_line"] == start
        assert result.value["end_line"] == end
        assert result.value["content"] == expected
        assert result.value["truncated"] is False
    assert artifact_result.memory_updates["read_artifact_ids"] == [ref.id]


def test_artifact_range_can_recover_text_after_default_character_limit(tmp_path):
    path = tmp_path / "large.txt"
    path.write_text("first " + "x" * 8100 + "\nsecond\nrequired evidence\n", encoding="utf-8")
    ref = _artifact(path)
    reader = RegisteredArtifactReader([ref], run_id=ref.run_id)
    default = reader.read_text(ref.id)
    assert len(default["content"]) == 8000
    assert default["truncated"] is True
    assert default["start_line"] is default["end_line"] is None
    ranged = reader.read_text(ref.id, start_line=2, end_line=3)
    assert ranged["content"] == "second\nrequired evidence\n"
    assert ranged["truncated"] is False
    assert reader.read_text(ref.id, start_line=2, end_line=3, max_chars=5)["truncated"] is True


def test_artifact_range_verifies_bytes_outside_selected_lines(tmp_path):
    path = tmp_path / "frozen.txt"
    path.write_text("selected line\nuntouched line\nlast line\n", encoding="utf-8")
    ref = _artifact(path)
    path.write_text("selected line\nuntouched line\nTAMPERED\n", encoding="utf-8")
    reader = RegisteredArtifactReader([ref], run_id=ref.run_id)
    with pytest.raises(ArtifactReadError, match="sha256"):
        reader.read_text(ref.id, start_line=1, end_line=1)


@pytest.mark.parametrize("live_resolver", [False, True])
def test_artifact_range_rejects_cross_run_before_reading(tmp_path, monkeypatch, live_resolver):
    path = tmp_path / "frozen.txt"
    path.write_text("must not read\n", encoding="utf-8")
    ref = _artifact(path)
    reader = RegisteredArtifactReader(
        [] if live_resolver else [ref], run_id="run_other",
        resolve=(lambda _: ref) if live_resolver else None,
    )
    def forbidden_read(self):
        raise AssertionError("Unauthorized artifact bytes were read")
    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    with pytest.raises(ArtifactReadError, match="unknown artifact"):
        reader.read_text(ref.id, start_line=1, end_line=1)


def test_artifact_inverted_range_is_a_controlled_error(tmp_path):
    path = tmp_path / "frozen.txt"
    path.write_text("one\ntwo\n", encoding="utf-8")
    ref = _artifact(path)
    tool = ReadArtifactTool(RegisteredArtifactReader([ref], run_id=ref.run_id))
    with pytest.raises(ValueError, match="end_line must be"):
        tool.execute(_state(), tool.input_model(artifact_id=ref.id, start_line=2, end_line=1))


def test_artifact_tool_rejects_zero_line_in_schema(tmp_path):
    tool = ReadArtifactTool(RegisteredArtifactReader([], run_id="run_reader"))
    with pytest.raises(ValidationError):
        tool.input_model(artifact_id="artifact_text", start_line=0)


def test_search_accepts_a_single_file_or_directory(tmp_path):
    (tmp_path / "train.py").write_text("nothing\nTarget = 1\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("target = 2\n", encoding="utf-8")
    tool = SearchTextTool(_boundary(tmp_path))
    one = tool.execute(_state(), tool.input_model(path="train.py", query="target"))
    assert one.value["matches"] == [{"path": "train.py", "line": 2, "text": "Target = 1"}]
    assert one.memory_updates["read_paths"] == ["train.py"]
    both = tool.execute(_state(), tool.input_model(path=".", query="target"))
    assert {item["path"] for item in both.value["matches"]} == {"train.py", "other.py"}


@pytest.mark.parametrize("query_path", ["denied/secret.py", "alias.py", "escape.py", "../outside.py", ".git/config"])
def test_single_file_search_cannot_bypass_path_scope(tmp_path, query_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "denied").mkdir()
    denied = root / "denied" / "secret.py"
    denied.write_text("target = secret\n")
    outside = tmp_path / "outside.py"
    outside.write_text("target = outside\n")
    (root / "alias.py").symlink_to(denied)
    (root / "escape.py").symlink_to(outside)
    tool = SearchTextTool(_boundary(root, denied_paths=["denied"]))
    with pytest.raises(WorkspacePermissionError):
        tool.execute(_state(), tool.input_model(path=query_path, query="target"))


def test_single_file_search_respects_allowed_paths(tmp_path):
    (tmp_path / "allowed.py").write_text("target = 1\n")
    (tmp_path / "other.py").write_text("target = 2\n")
    tool = SearchTextTool(_boundary(tmp_path, allowed_paths=["allowed.py"]))
    assert tool.execute(_state(), tool.input_model(path="allowed.py", query="target")).ok
    with pytest.raises(WorkspacePermissionError):
        tool.execute(_state(), tool.input_model(path="other.py", query="target"))


def test_search_guidance_matches_bounded_input_schema(tmp_path):
    tool = SearchTextTool(_boundary(tmp_path))
    assert "file or directory" in tool.model_guidance
    assert "50" in tool.model_guidance
    with pytest.raises(ValidationError):
        tool.input_model(query="target", max_results=51)
