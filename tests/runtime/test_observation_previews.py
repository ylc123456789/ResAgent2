"""Bounded tool-history previews preserve provenance without claiming completeness."""

from datetime import UTC, datetime

from resagent2_contracts import AgentOwner
from resagent2_runtime import AgentEvent, AgentLoop, AgentState


def _state() -> AgentState:
    now = datetime.now(UTC)
    events = [
        AgentEvent(
            sequence=sequence, step=step, type="observation", tool=tool,
            data={"ok": True, "summary": tool, "value": value}, created_at=now,
        )
        for sequence, step, tool, value in [
            (2, 1, "read_file", {"path": "a.py", "content": "old", "truncated": False}),
            (7, 3, "replace_text", {"path": "a.py"}),
            (12, 5, "read_file", {"path": "a.py", "content": "new", "truncated": False}),
        ]
    ]
    return AgentState(
        session_id="session_previews", agent_name="reader", owner=AgentOwner.CODING,
        run_id="run_previews", task_id="task_previews", attempt_number=1,
        events=events, created_at=now, updated_at=now,
    )


def test_recent_history_keeps_actual_event_sequences_and_chronology() -> None:
    state = _state()
    section = AgentLoop._recent_observations_section(state, limit=2)
    assert section is not None
    assert "Event 2." not in section.content
    assert "Event 7. replace_text" in section.content
    assert "Event 12. read_file" in section.content
    assert section.content.index("Event 7.") < section.content.index("Event 12.")
    assert "session event sequence" in section.content


def test_history_preview_truncation_does_not_change_source_completeness() -> None:
    state = _state()
    source = state.events[-1].data["value"]
    source["content"] = "begin " + "x" * 1000 + " end"
    before = state.model_dump(mode="json")
    section = AgentLoop._recent_observations_section(state, limit=1)
    assert section is not None
    assert "bounded history previews, not complete tool results" in section.content
    assert "ellipsis here means preview truncation" in section.content
    assert "original read result's truncated flag" in section.content
    assert "Value preview: " in section.content
    assert " … " in section.content
    assert '"truncated": false' in section.content
    assert source["content"] not in section.content
    assert state.model_dump(mode="json") == before
