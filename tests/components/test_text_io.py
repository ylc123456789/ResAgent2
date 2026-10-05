"""Bound workspace reads by actual bytes without narrowing frozen evidence."""

import hashlib
from pathlib import Path

import pytest

from resagent2_components import RegisteredArtifactReader
from resagent2_components.text import MAX_WORKSPACE_TEXT_BYTES, TextFileTooLargeError, read_text_file
from resagent2_contracts import AgentOwner, ArtifactRef


def test_workspace_read_rejects_growth_after_size_precheck(tmp_path, monkeypatch):
    path = tmp_path / "growing.txt"
    path.write_bytes(b"small")
    original_stat = Path.stat
    grown = False

    def growing_stat(source, *args, **kwargs):
        nonlocal grown
        result = original_stat(source, *args, **kwargs)
        if source == path and not grown:
            grown = True
            path.write_bytes(b"x" * 100)
        return result

    monkeypatch.setattr(Path, "stat", growing_stat)
    with pytest.raises(TextFileTooLargeError, match="too large"):
        read_text_file(path, max_bytes=10)
    assert path.read_bytes() == b"x" * 100


def test_workspace_processing_limit_does_not_restrict_registered_text(tmp_path):
    path = tmp_path / "frozen.txt"
    content = b"selected\r\n" + b"x" * MAX_WORKSPACE_TEXT_BYTES
    path.write_bytes(content)
    ref = ArtifactRef(
        id="artifact_text", kind="text", producer=AgentOwner.EXPERIMENT,
        run_id="run_reader", task_id="task_reader", attempt_number=1,
        uri=path.as_uri(), sha256=hashlib.sha256(content).hexdigest(),
        media_type="text/plain", summary="Frozen evidence",
    )
    reader = RegisteredArtifactReader([ref], run_id=ref.run_id)
    result = reader.read_text(ref.id, start_line=1, end_line=1)
    assert result["content"] == "selected\r\n"
    assert result["truncated"] is False
