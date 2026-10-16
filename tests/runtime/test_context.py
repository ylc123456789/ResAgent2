"""Lossless fixed-context composition."""
import pytest
from resagent2_runtime import ContextBudgetExceeded, ContextComposer, ContextSection

def test_context_rejects_zero_budget():
    with pytest.raises(ContextBudgetExceeded):
        ContextComposer().compose("rules", [], max_tokens=0)

def test_complete_sections_keep_order_and_content():
    sections = [ContextSection(name="task", content="original task"),
                ContextSection(name="artifact_index", content="all IDs")]
    result = ContextComposer().compose("rules", sections, max_tokens=1000)
    assert result.included_sections == ["system", "task", "artifact_index"]
    assert result.omitted_sections == []
    assert result.task_context == "## task\noriginal task"
    assert result.artifact_index == "## artifact_index\nall IDs"

def test_exact_budget_counts_headings_and_separators():
    composer = ContextComposer()
    sections = [ContextSection(name="task", content="complete")]
    exact = composer.compose("rules", sections, max_tokens=1000).estimated_tokens
    assert composer.compose("rules", sections, max_tokens=exact).estimated_tokens == exact
    with pytest.raises(ContextBudgetExceeded):
        composer.compose("rules", sections, max_tokens=exact-1)

def test_duplicate_section_names_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        ContextComposer().compose("rules", [ContextSection(name="task",content="a"),
                                          ContextSection(name="task",content="b")], max_tokens=1000)
