"""Shell argument helpers: token splitting and answer-value resolution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from resagent2_cli.shell import (
    _answer_values,
    _flag_value,
    _reject_shell_data_root,
    _split_answer_tokens,
)


def _run(requested_fields=None):
    if requested_fields is None:
        return SimpleNamespace(pending_question=None)
    return SimpleNamespace(
        pending_question=SimpleNamespace(
            id="question_1", text="q", requested_fields=requested_fields
        )
    )


def test_split_answer_tokens_single_field_with_workspace():
    fields, ws = _split_answer_tokens(["accuracy", "--workspace", "/tmp"])
    assert fields == ["accuracy"]
    assert ws == ["--workspace", "/tmp"]


def test_split_answer_tokens_name_value_pairs():
    fields, ws = _split_answer_tokens(["metric=accuracy", "seed=42"])
    assert fields == ["metric=accuracy", "seed=42"]
    assert ws == []


def test_split_answer_tokens_mixed():
    fields, ws = _split_answer_tokens(
        ["metric=accuracy", "--git", "https://x", "seed=1"]
    )
    assert fields == ["metric=accuracy", "seed=1"]
    assert ws == ["--git", "https://x"]


def test_flag_value():
    assert _flag_value(["--workspace", "/tmp"], "--workspace") == "/tmp"
    assert _flag_value([], "--workspace") is None
    assert _flag_value(["--git"], "--git") is None


def test_answer_values_single_field_shorthand():
    assert _answer_values(_run(["primary_metric"]), ["accuracy"]) == {
        "primary_metric": "accuracy"
    }


def test_answer_values_shorthand_rejects_multiple_requested_fields():
    with pytest.raises(ValueError):
        _answer_values(_run(["a", "b"]), ["accuracy"])


def test_answer_values_explicit_name_value():
    assert _answer_values(_run(["a", "b"]), ["a=1", "b=2"]) == {"a": "1", "b": "2"}


def test_answer_values_rejects_missing_pending_question():
    with pytest.raises(ValueError):
        _answer_values(_run(None), ["accuracy"])


def test_shell_data_root_is_fixed_at_startup():
    with pytest.raises(ValueError, match="fixed at startup"):
        _reject_shell_data_root(["--data-root", "/other"])
