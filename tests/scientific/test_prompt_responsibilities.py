"""Check published guidance, not whether a real model will follow it."""

from resagent2_capabilities import ReplaceTextTool
from resagent2_coding.verification import RunVerificationTool
from resagent2_scientific.context import SCIENTIFIC_PROMPT


# Line wrapping is presentation; these checks cover a few prior failure modes.
SCIENTIFIC_GUIDANCE = " ".join(SCIENTIFIC_PROMPT.split())


def test_scientific_prompt_keeps_own_tool_failures_out_of_request_work() -> None:
    assert "not a substitute for your own tools" in SCIENTIFIC_GUIDANCE
    assert "not a reason to delegate literature work" in SCIENTIFIC_GUIDANCE
    assert "tool's own retries are exhausted" in SCIENTIFIC_GUIDANCE
    assert "ask_user: explain the actual error" in SCIENTIFIC_GUIDANCE
    assert "explicit decision on proceeding with limited evidence" in SCIENTIFIC_GUIDANCE


def test_scientific_prompt_distinguishes_previews_from_visible_evidence() -> None:
    assert "short search previews do not establish" in SCIENTIFIC_GUIDANCE
    assert "Reading records are historical logs" in SCIENTIFIC_GUIDANCE
    assert "Read the needed source sections" in SCIENTIFIC_GUIDANCE
    assert "do not guess omitted content" in SCIENTIFIC_GUIDANCE


def test_scientific_prompt_separates_known_prerequisites_from_contingencies() -> None:
    assert "Include already-known prerequisites" in SCIENTIFIC_GUIDANCE
    assert "change before the experiment that needs it" in SCIENTIFIC_GUIDANCE
    assert "Do not run a known-broken experiment" in SCIENTIFIC_GUIDANCE
    assert "Distinguish known prerequisites from hypothetical failures" in SCIENTIFIC_GUIDANCE
    assert "request repair only after a failure is observed" in SCIENTIFIC_GUIDANCE


def test_edit_guidance_allows_multiple_uniquely_matching_edits() -> None:
    guidance = ReplaceTextTool.model_guidance
    assert "old_text must match exactly once" in guidance
    assert "current file per call" in guidance
    assert "Multiple calls are allowed" in guidance


def test_reports_carry_relevant_limits_without_becoming_independent_measurements() -> None:
    assert "carry relevant limitations into your judgment" in SCIENTIFIC_GUIDANCE
    assert "not independent measurements" in SCIENTIFIC_GUIDANCE
    assert "same data do not provide independent confirmation" in SCIENTIFIC_GUIDANCE
    assert "Read historical reports only when needed" in SCIENTIFIC_GUIDANCE


def test_verification_guidance_does_not_offer_forbidden_inline_python() -> None:
    guidance = RunVerificationTool.model_guidance
    assert "write a unittest with meaningful assertions" in guidance
    assert "python -c and arbitrary scripts are not allowed verification commands" in guidance
