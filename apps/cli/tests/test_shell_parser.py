"""Both CLI entrypoints parse answers and workspace flags through argparse."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from resagent2_cli.main import _answer_from_args, _parser
from resagent2_cli.shell import _NoExitParser, _reject_shell_data_root
from resagent2_contracts import PendingQuestion


def _answer(tokens, requested_fields=None):
    question = None if requested_fields is None else PendingQuestion(
        id="question_1", run_id="run_x", text="Choose a value",
        requested_fields=requested_fields, created_at=datetime.now(UTC),
    )
    run = SimpleNamespace(run_id="run_x", pending_question=question)
    args = _parser(_NoExitParser).parse_args(["answer", "run_x"] + tokens)
    return _answer_from_args(args, run)


@pytest.mark.parametrize("tokens", [["accuracy"], ["primary_metric=accuracy"],
                                   ["--field", "primary_metric=accuracy"]])
def test_answer_single_field_spellings(tokens):
    assert _answer(tokens, ["primary_metric"]).values == {"primary_metric": "accuracy"}


def test_answer_shorthand_rejects_multiple_requested_fields():
    with pytest.raises(ValueError, match="exactly one requested field"):
        _answer(["accuracy"], ["a", "b"])


def test_answer_explicit_fields_and_value_with_equals():
    assert _answer(["a=1", "b=second, mul=2*3"], ["a", "b"]).values == {
        "a": "1", "b": "second, mul=2*3",
    }


def test_answer_rejects_missing_pending_question():
    with pytest.raises(ValueError, match="no pending question"):
        _answer(["accuracy"])


def test_answer_rejects_empty_fields():
    with pytest.raises(ValueError, match="requires a value"):
        _answer([], ["metric"])


@pytest.mark.parametrize("tokens", [["accuracy", "--unknown"], ["accuracy", "--workspace"]])
def test_answer_rejects_invalid_flags(tokens):
    with pytest.raises(ValueError):
        _answer(tokens, ["metric"])


@pytest.mark.parametrize("tokens", [["--data-root", "/other"], ["--data-root=/other"]])
def test_shell_data_root_is_fixed_at_startup(tokens):
    with pytest.raises(ValueError, match="fixed at startup"):
        _reject_shell_data_root(tokens)
