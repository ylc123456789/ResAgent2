"""Shared bounded context helpers."""

from datetime import UTC, datetime

from resagent2_contracts import AgentOwner
from resagent2_runtime import (
    AgentEvent,
    AgentState,
    recent_tool_listing,
    recent_tool_snippets,
)


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
        ("a.py", 100, 140),
        ("a.py", 180, 210),
    ]
    assert [s["observed_at"] for s in snippets] == [1, 2]


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
    # Budget selection favors recent reads; presentation follows event order.
    assert [s["path"] for s in snippets] == ["big.py", "a.py", "b.py"]
    assert snippets[2]["content"] == "b" * 120
    assert snippets[1]["content"] == "a" * 120
    assert snippets[2]["truncated"] is False
    assert snippets[1]["truncated"] is False
    assert snippets[0]["truncated"] is True
    assert snippets[0]["context_truncated"] is True
    assert snippets[0]["content"].startswith("X")
    assert snippets[0]["content"].endswith("X")
    assert sum(len(s["content"]) for s in snippets) == 6000
    # The source tool result remains complete in the durable event history.
    original = state.events[0].data["value"]
    assert original["truncated"] is False
    assert len(original["content"]) == 8000
    assert "observed_at" not in original
    assert "context_truncated" not in original


def test_snippets_deduplicate_ranges_but_keep_original_event_ids() -> None:
    state = _snippet_state(
        {"path": "a.py", "content": "first read"},
        {"path": "b.py", "content": "other file"},
        {"path": "a.py", "content": "latest read"},
    )
    for event, sequence in zip(state.events, (10, 20, 30)):
        event.sequence = sequence
    before = state.model_dump(mode="json")
    snippets = recent_tool_snippets(
        state, tool="read_file", identity_keys=("path",), text_key="content",
    )
    assert [(s["content"], s["observed_at"]) for s in snippets] == [
        ("other file", 20), ("latest read", 30),
    ]
    # Projection values are copies, including the untruncated fast path.
    snippets[0]["content"] = "caller mutation"
    assert state.model_dump(mode="json") == before


def test_snippet_limit_still_selects_most_recent_observations() -> None:
    state = _snippet_state(*[
        {"path": f"{index}.py", "content": str(index)} for index in range(5)
    ])
    snippets = recent_tool_snippets(
        state, tool="read_file", identity_keys=("path",), text_key="content", limit=2,
    )
    assert [s["observed_at"] for s in snippets] == [4, 5]


def test_tool_truncation_does_not_claim_extra_context_truncation() -> None:
    state = _snippet_state({"path": "a.py", "content": "short prefix", "truncated": True})
    snippet = recent_tool_snippets(
        state, tool="read_file", identity_keys=("path",), text_key="content",
    )[0]
    assert snippet["truncated"] is True
    assert "context_truncated" not in snippet


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


def test_recent_listing_bounds_total_chars_without_truncating_entries() -> None:
    state = _listing_state(
        {"path": ".", "paths": ["aaa.py", "bbb.py", "ccc.py"], "truncated": False}
    )
    listing = recent_tool_listing(
        state, tool="list_files", list_key="paths", max_entries=80, max_chars=12
    )
    # 6 + 6 fits under 12; the third 6-char path does not, and is dropped whole.
    assert listing["paths"] == ["aaa.py", "bbb.py"]
    assert listing["truncated"] is True
    assert listing["path"] == "."
