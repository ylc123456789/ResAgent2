"""Shared bounded context helpers."""

from datetime import UTC, datetime
import json

from resagent2_contracts import AgentOwner
from resagent2_runtime import (
    AgentEvent,
    AgentState,
    ContextComposer,
    ContextSection,
    recent_tool_listing,
    recent_tool_snippets,
    recent_tool_text_values,
)


def _state(*contents: tuple[str, str]) -> AgentState:
    now = datetime.now(UTC)
    events = [
        AgentEvent(
            sequence=index,
            step=index,
            type="observation",
            tool="read_file",
            data={"value": {"path": path, "content": content}},
            created_at=now,
        )
        for index, (path, content) in enumerate(contents, start=1)
    ]
    return AgentState(
        session_id="session_context",
        agent_name="test",
        owner=AgentOwner.CODING,
        run_id="run_context",
        task_id="task_context",
        attempt_number=1,
        events=events,
        created_at=now,
        updated_at=now,
    )


def test_recent_tool_text_values_uses_one_total_budget_and_keeps_head_tail() -> None:
    values = recent_tool_text_values(
        _state(("a.py", "A" * 5000 + "TAIL_A"), ("b.py", "B" * 5000 + "TAIL_B")),
        tool="read_file",
        identity_key="path",
        text_key="content",
        max_total_chars=2000,
    )
    assert set(values) == {"a.py", "b.py"}
    assert sum(len(value) for value in values.values()) <= 2000
    assert values["a.py"].endswith("TAIL_A")
    assert values["b.py"].endswith("TAIL_B")


def test_bounded_read_section_fits_required_context_budget() -> None:
    values = recent_tool_text_values(
        _state(("large.py", "x" * 20000)),
        tool="read_file",
        identity_key="path",
        text_key="content",
    )
    section = ContextSection(
        name="read_files",
        content=json.dumps(values),
        required=True,
    )
    composed = ContextComposer().compose(
        "system",
        [ContextSection(name="task", content="goal", required=True), section],
        max_tokens=4096,
    )
    assert "read_files" in composed.included_sections
    assert composed.estimated_tokens <= 4096


def _snippet_state(*values: dict) -> AgentState:
    now = datetime.now(UTC)
    events = [
        AgentEvent(
            sequence=index,
            step=index,
            type="observation",
            tool="read_file",
            data={"value": value},
            created_at=now,
        )
        for index, value in enumerate(values, start=1)
    ]
    return AgentState(
        session_id="session_context",
        agent_name="test",
        owner=AgentOwner.CODING,
        run_id="run_context",
        task_id="task_context",
        attempt_number=1,
        events=events,
        created_at=now,
        updated_at=now,
    )


def test_snippets_keep_two_ranges_of_the_same_file() -> None:
    state = _snippet_state(
        {
            "path": "a.py",
            "start_line": 100,
            "end_line": 140,
            "content": "A" * 500,
            "truncated": False,
        },
        {
            "path": "a.py",
            "start_line": 180,
            "end_line": 210,
            "content": "B" * 500,
            "truncated": False,
        },
    )
    snippets = recent_tool_snippets(
        state,
        tool="read_file",
        identity_keys=("path", "start_line", "end_line"),
        text_key="content",
    )
    assert [(s["path"], s["start_line"], s["end_line"]) for s in snippets] == [
        ("a.py", 180, 210),
        ("a.py", 100, 140),
    ]


def test_snippets_pack_whole_then_truncate_newest_first() -> None:
    state = _snippet_state(
        {
            "path": "big.py",
            "start_line": 1,
            "end_line": None,
            "content": "X" * 8000,
            "truncated": False,
        },
        {
            "path": "a.py",
            "start_line": 1,
            "end_line": None,
            "content": "a" * 120,
            "truncated": False,
        },
        {
            "path": "b.py",
            "start_line": 1,
            "end_line": None,
            "content": "b" * 120,
            "truncated": False,
        },
    )
    snippets = recent_tool_snippets(
        state,
        tool="read_file",
        identity_keys=("path", "start_line", "end_line"),
        text_key="content",
        max_total_chars=6000,
    )
    # Newest first, small snippets whole, only the older large one truncated.
    assert [s["path"] for s in snippets] == ["b.py", "a.py", "big.py"]
    assert snippets[0]["content"] == "b" * 120
    assert snippets[1]["content"] == "a" * 120
    assert snippets[0]["truncated"] is False
    assert snippets[1]["truncated"] is False
    assert snippets[2]["truncated"] is True
    assert snippets[2]["content"].startswith("X")
    assert snippets[2]["content"].endswith("X")


def _listing_state(*values: dict) -> AgentState:
    now = datetime.now(UTC)
    events = [
        AgentEvent(
            sequence=index,
            step=index,
            type="observation",
            tool="list_files",
            data={"value": value},
            created_at=now,
        )
        for index, value in enumerate(values, start=1)
    ]
    return AgentState(
        session_id="session_context",
        agent_name="test",
        owner=AgentOwner.CODING,
        run_id="run_context",
        task_id="task_context",
        attempt_number=1,
        events=events,
        created_at=now,
        updated_at=now,
    )


def test_recent_listing_keeps_latest_and_bounds() -> None:
    state = _listing_state(
        {"paths": ["a.py", "b.py"], "truncated": False},
        {"paths": ["c.py", "d.py", "e.py"], "truncated": False},
    )
    listing = recent_tool_listing(
        state, tool="list_files", list_key="paths", max_entries=2
    )
    # Latest wins, bounded to 2 entries, truncated because 3 > 2.
    assert listing["paths"] == ["c.py", "d.py"]
    assert listing["truncated"] is True


def test_recent_listing_preserves_tool_truncation() -> None:
    state = _listing_state({"paths": ["a.py"], "truncated": True})
    listing = recent_tool_listing(
        state, tool="list_files", list_key="paths", max_entries=80
    )
    assert listing["paths"] == ["a.py"]
    assert listing["truncated"] is True


def test_recent_listing_returns_none_when_absent() -> None:
    assert recent_tool_listing(
        _listing_state(), tool="list_files", list_key="paths"
    ) is None
