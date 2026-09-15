"""One full-request budget, soft shares, and no state or protocol rewriting."""

import json

import pytest

from resagent2_runtime import ContextBudgetExceeded, ContextComposer, ContextMaterial, ContextSection
from resagent2_runtime.compaction import plan_compaction
from resagent2_runtime.models import NativeToolCall, ToolCallTurn
from resagent2_runtime.tool_calling import native_input_text


def _material(name, body, *, weight=1, priority=0):
    return ContextMaterial(
        name=name, render=lambda chars: json.dumps({"body": body[:chars], "omitted": chars < len(body)}),
        weight=weight, priority=priority,
    )


def _body(context, name):
    section = context.text.split(f"## {name}\n", 1)[1].split("\n\n## ", 1)[0]
    return json.loads(section)["body"]


def test_spare_share_is_borrowed_without_dropping_the_small_material():
    composer = ContextComposer()
    context = composer.compose("rules", [
        _material("files", "f" * 100_000), _material("artifacts", "short evidence"),
    ], max_tokens=2000)
    assert _body(context, "artifacts") == "short evidence"
    assert len(_body(context, "files")) > 4000  # More than half of the whole input.
    assert context.estimated_tokens <= 1600


def test_unused_share_goes_by_priority_after_every_material_gets_a_share():
    context = ContextComposer().compose("rules", [
        _material("low", "l" * 100_000, priority=1),
        _material("high", "h" * 100_000, priority=20),
        _material("small", "s"),
    ], max_tokens=3000)
    low, high = len(_body(context, "low")), len(_body(context, "high"))
    assert low > 2000
    assert high > low * 1.5
    assert _body(context, "small") == "s"
    assert context.estimated_tokens <= 2400


def test_fixed_required_context_can_exceed_soft_target_but_not_hard_limit():
    composer = ContextComposer()
    sections = [ContextSection(name="answers", content="q:a" * 120, required=True)]
    exact = composer.compose("rules", sections, max_tokens=200).estimated_tokens
    context = composer.compose("rules", sections, max_tokens=exact)
    assert context.estimated_tokens == exact
    with pytest.raises(ContextBudgetExceeded, match="answers"):
        composer.compose("rules", sections, max_tokens=exact - 1)


def test_large_required_input_reduces_material_instead_of_being_clipped():
    fixed = ContextSection(name="answers", content="original question and answer " * 120, required=True)
    context = ContextComposer().compose("rules", [fixed, _material("files", "f" * 100_000)], max_tokens=2000)
    assert fixed.content in context.text
    assert 0 < len(_body(context, "files")) < 4000
    assert context.estimated_tokens <= 1600


@pytest.mark.parametrize("body", ['quote"\\\n' * 2000, "中文" * 10000])
def test_native_wire_overhead_and_escaping_share_the_same_budget(body):
    turn = ToolCallTurn(
        tool_calls=[NativeToolCall(id="call_1", name="read", arguments='{"path":"x"}')],
        tool_results={"call_1": '{"ok":true,"value":"source"}'},
    )
    before = turn.model_dump_json()
    older = ToolCallTurn(
        tool_calls=[NativeToolCall(id="call_0", name="read", arguments='{"path":"old"}')],
        tool_results={"call_0": '{"ok":true,"value":"old source"}'},
    )
    turns = [older, turn]
    schemas = [{"type": "function", "function": {"name": "read", "description": "d" * 500}}]
    measure = lambda text: ContextComposer.estimate_tokens(native_input_text(text, schemas, turns))
    context = ContextComposer().compose("rules", [_material("files", body)], max_tokens=3000, measure=measure)
    assert context.estimated_tokens == measure(context.text) <= 2400
    assert _body(context, "files")
    assert turn.model_dump_json() == before
    # Expansion alone must not trigger compaction; recent protocol groups stay whole.
    assert plan_compaction(current_context=context.text, schemas=schemas, turns=turns, max_input_tokens=3000) is None


def test_required_material_frame_too_large_fails_without_calling_large_render():
    calls = []
    def render(chars):
        calls.append(chars)
        return "frame" * 100
    with pytest.raises(ContextBudgetExceeded, match="source"):
        ContextComposer().compose("rules", [ContextMaterial(name="source", render=render)], max_tokens=50)
    assert calls == [0]


def test_material_rendering_is_repeatable_and_does_not_change_definitions():
    material = _material("files", "known content")
    composer = ContextComposer()
    small = composer.compose("rules", [material], max_tokens=60)
    large = composer.compose("rules", [material], max_tokens=2000)
    assert composer.compose("rules", [material], max_tokens=60) == small
    assert _body(large, "files") == "known content"
    assert large.estimated_tokens < 100


@pytest.mark.parametrize("weight", [0, -1, float("nan"), float("inf")])
def test_material_rejects_invalid_allocation_weights(weight):
    with pytest.raises(ValueError, match="weight"):
        _material("file", "text", weight=weight)
