"""Shared bounded context helpers."""

from datetime import UTC, datetime
import json

from resagent2_contracts import AgentOwner
from resagent2_runtime import (
    AgentEvent,
    AgentState,
    ContextComposer,
    ContextSection,
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
