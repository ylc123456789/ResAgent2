import copy

from resagent2_components import (
    RegisteredArtifactReader,
    build_module_report,
)
from resagent2_components.artifacts import REPORT_LINE_CHARS
from resagent2_contracts import AgentOwner
from resagent2_orchestrator import ArtifactRegistry


def test_module_report_preserves_selected_details_without_mutation() -> None:
    details = {
        "answer": "Read the implementation.\nIt returns the sum.",
        "uncertainty": "Unicode and newlines: \u4ec5\u9759\u6001\u68c0\u67e5\nnot executed",
        "evidence_files": ["util.py"],
    }
    before = copy.deepcopy(details)

    candidate = build_module_report(details)

    assert details == before
    assert candidate.kind == "module_report"
    assert candidate.path == "module_report.md"
    assert candidate.media_type == "text/markdown"
    assert candidate.metadata == {}
    assert "not measured evidence" in candidate.summary
    assert "not independently verified or measured evidence" in candidate.content
    assert "## answer\n\n" + before["answer"] in candidate.content
    assert "## uncertainty\n\n" + before["uncertainty"] in candidate.content
    assert "## evidence_files\n\n- util.py\n" in candidate.content
    details["evidence_files"].append("later.py")
    assert "later.py" not in candidate.content


def test_module_report_does_not_truncate_explanation() -> None:
    answer = "analysis " * 2000
    candidate = build_module_report({"answer": answer})
    body = candidate.content.split("## answer\n\n", 1)[1].removesuffix("\n")
    assert "".join(body.split("\n")) == answer
    assert all(len(line) <= REPORT_LINE_CHARS for line in body.split("\n"))


def test_module_report_keeps_blank_lines_and_whitespace() -> None:
    prefix = "  first\t \n\n"
    long_line = " x\t" * REPORT_LINE_CHARS
    suffix = "\n\n  final \t "
    candidate = build_module_report({"answer": prefix + long_line + suffix})
    body = candidate.content.split("## answer\n\n", 1)[1].removesuffix("\n")
    assert body.startswith(prefix)
    assert body.endswith(suffix)
    assert "".join(body[len(prefix):-len(suffix)].split("\n")) == long_line


def test_long_report_tail_is_reachable_through_registered_line_read(tmp_path) -> None:
    sentinel = "TAIL_SENTINEL_9c26"
    candidate = build_module_report({
        "answer": "x" * (REPORT_LINE_CHARS * 140) + sentinel,
        "uncertainty": "Only inspected source",
    })
    registry = ArtifactRegistry(tmp_path / "artifacts")
    artifact = registry.register(
        candidate,
        grant=None,
        producer=AgentOwner.CODING,
        run_id="run_report",
        task_id="task_inspect",
        attempt_number=1,
        index=1,
        existing_ids=set(),
    )
    reader = RegisteredArtifactReader([artifact], run_id="run_report")
    first = reader.read_text(artifact.id)
    assert first["truncated"]
    assert sentinel not in first["content"]
    tail_line = next(
        index for index, line in enumerate(candidate.content.splitlines(), 1)
        if sentinel in line
    )

    tail = reader.read_text(artifact.id, start_line=tail_line, end_line=tail_line)

    assert not tail["truncated"]
    assert sentinel in tail["content"]
    assert tail["start_line"] == tail_line
    assert tail["end_line"] == tail_line
