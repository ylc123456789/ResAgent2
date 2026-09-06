"""Check published guidance, not whether a real model will follow it."""

from resagent2_coding.context import MODIFY_PROMPT
from resagent2_scientific.context import SCIENTIFIC_PROMPT


def test_scientific_prompt_keeps_own_tool_failures_out_of_request_work() -> None:
    assert "not a substitute for your own tools" in SCIENTIFIC_PROMPT
    assert "HTTP 429" in SCIENTIFIC_PROMPT
    assert "not a reason to delegate literature work" in SCIENTIFIC_PROMPT
    assert "tool's own retries are exhausted" in SCIENTIFIC_PROMPT
    assert "ask_user: explain the actual error" in SCIENTIFIC_PROMPT
    assert "service is restored before continuing" in SCIENTIFIC_PROMPT


def test_scientific_prompt_distinguishes_previews_from_visible_evidence() -> None:
    assert "short result preview is not proof" in SCIENTIFIC_PROMPT
    assert "An observed id records past access" in SCIENTIFIC_PROMPT
    assert "needed start_line/end_line range" in SCIENTIFIC_PROMPT
    assert "do not guess the missing contents" in SCIENTIFIC_PROMPT


def test_coding_prompt_allows_multiple_uniquely_matching_edits() -> None:
    assert "old_text must match exactly once" in MODIFY_PROMPT
    assert "current file per call" in MODIFY_PROMPT
    assert "multiple replace_text calls as needed" in MODIFY_PROMPT
    assert "exactly-once replace_text action" not in MODIFY_PROMPT
