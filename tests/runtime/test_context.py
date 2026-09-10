"""Shared bounded context helpers."""

import json
from datetime import UTC, datetime

import pytest

from resagent2_contracts import AgentOwner, UserAnswer
from resagent2_runtime import (
    AgentEvent,
    AgentState,
    ContextBudgetExceeded,
    ContextComposer,
    ContextSection,
    recent_tool_listing,
    recent_tool_snippets,
    user_answers_section,
)


def test_user_answers_section_is_absent_without_supplied_answers():
    assert user_answers_section([]) is None


def test_user_answers_section_preserves_supplied_replies_and_order():
    now = datetime.now(UTC)
    answers = [
        UserAnswer(question_id="question_first", values={"choice": "甲"}, answered_at=now),
        UserAnswer(question_id="question_second", values={"choice": "乙"}, answered_at=now),
    ]
    before = [answer.model_dump(mode="json") for answer in answers]

    section = user_answers_section(answers)

    assert section.name == "answers"
    assert section.required
    guidance, payload = section.content.split("\n", 1)
    assert "An earlier ask_user [ok] only means a question was issued" in guidance
    assert "current checked context" in guidance
    assert json.loads(payload) == before
    assert [answer.model_dump(mode="json") for answer in answers] == before
    # No reply is retained across invocations or borrowed from another task.
    other = user_answers_section([answers[1]])
    assert "question_first" not in other.content
    assert user_answers_section([]) is None


def test_user_answers_share_composer_budget_and_are_not_optional():
    answer = UserAnswer(
        question_id="question_choice", values={"choice": "keep existing format"},
        answered_at=datetime.now(UTC),
    )
    section = user_answers_section([answer])
    composer = ContextComposer()
    expected = composer.compose("system", [section], max_tokens=1000)
    with_optional = composer.compose(
        "system", [ContextSection(name="optional", content="x" * 10000), section],
        max_tokens=expected.estimated_tokens,
    )
    assert with_optional.text == expected.text
    assert with_optional.included_sections == ["system", "answers"]
    assert with_optional.omitted_sections == ["optional"]
    # Required answers fail explicitly instead of disappearing or being clipped.
    with pytest.raises(ContextBudgetExceeded, match="answers"):
        composer.compose("system", [section], max_tokens=expected.estimated_tokens - 1)


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


def test_context_rejects_zero_budget() -> None:
    with pytest.raises(ContextBudgetExceeded, match="positive"):
        ContextComposer().compose("", [], max_tokens=0)


def test_empty_system_still_accounts_for_its_heading() -> None:
    composer = ContextComposer()
    context = composer.compose("", [], max_tokens=3)
    assert context.text == "## system\n"
    assert context.estimated_tokens == composer.estimate_tokens(context.text) == 3
    with pytest.raises(ContextBudgetExceeded, match="system"):
        composer.compose("", [], max_tokens=2)


def test_required_context_includes_separator_at_budget_boundary() -> None:
    composer = ContextComposer()
    sections = [ContextSection(name="goal", content="", required=True)]
    context = composer.compose("ab", sections, max_tokens=6)
    # The two sections alone are 20 characters; their separator makes 22.
    assert context.text == "## system\nab\n\n## goal\n"
    assert context.estimated_tokens == composer.estimate_tokens(context.text) == 6
    assert context.included_sections == ["system", "goal"]
    with pytest.raises(ContextBudgetExceeded, match="goal"):
        composer.compose("ab", sections, max_tokens=5)


def test_context_skips_large_optional_and_keeps_priority_tie_order() -> None:
    composer = ContextComposer()
    large_name = "optional_heading_exceeds_remaining_budget"
    sections = [
        ContextSection(name="first", content="", priority=1),
        ContextSection(name=large_name, content="", priority=10),
        ContextSection(name="last", content="", priority=1),
    ]
    context = composer.compose("ab", sections, max_tokens=6)
    assert context.included_sections == ["system", "first"]
    assert context.omitted_sections == [large_name, "last"]
    assert context.text == "## system\nab\n\n## first\n"
    assert context.estimated_tokens == composer.estimate_tokens(context.text) == 6
