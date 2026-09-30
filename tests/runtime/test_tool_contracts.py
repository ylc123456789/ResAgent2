"""Tests for the model-facing per-tool argument contract."""

import pytest

from pydantic import BaseModel

from resagent2_capabilities import (
    AuditEnvTool, PrepareEnvironmentTool, ReplaceTextTool, RunSetupTool,
)

from resagent2_coding.verification import RunVerificationTool
from resagent2_experiment.tools import RunCommandTool
from resagent2_runtime import AgentAction, AskUserTool, FinishTool
from resagent2_scientific.tools import (
    AskUserTool as ScientificAskUserTool, RequestWorkTool,
)
from resagent2_runtime.tools import tool_contracts_text
from resagent2_runtime.tool_calling import native_tool_schemas


class _FinishInput(BaseModel):
    opinion: dict
    summary: str


class _FinishTool:
    name = "finish"
    input_model = _FinishInput


class _AskInput(BaseModel):
    text: str
    reason: str
    optional_note: str | None = None


class _AskTool:
    name = "ask_user"
    input_model = _AskInput


def test_tool_contracts_lists_required_arguments_only() -> None:
    text = tool_contracts_text((_FinishTool, _AskTool))

    assert "finish: opinion, summary" in text
    assert "ask_user: text, reason" in text
    # Optional / defaulted fields must not be presented as required.
    assert "optional_note" not in text


def test_agent_action_has_no_reasoning_summary() -> None:
    # The dead field must be gone so it no longer competes with a tool's
    # required `summary` argument.
    assert "reasoning_summary" not in AgentAction.model_fields


def test_ask_user_contract_requires_requested_fields() -> None:
    from resagent2_runtime import AskUserTool

    text = tool_contracts_text((AskUserTool(),))
    assert "ask_user: text, requested_fields" in text
    assert "reason" not in text


def test_tool_contracts_include_optional_model_guidance() -> None:
    class _ReadInput(BaseModel):
        path: str

    class _ReadTool:
        name = "read_file"
        input_model = _ReadInput
        model_guidance = "read a bounded start_line/end_line range when truncated"

    text = tool_contracts_text((_ReadTool, _FinishTool))
    assert "- read_file: path" in text
    assert "read a bounded start_line/end_line range when truncated" in text
    # A tool without guidance still emits only its one contract line.
    assert "- finish: opinion, summary" in text


@pytest.mark.parametrize("tool_class", [
    PrepareEnvironmentTool, AuditEnvTool, RunSetupTool, ReplaceTextTool,
    RequestWorkTool, ScientificAskUserTool, FinishTool, AskUserTool,
    RunVerificationTool, RunCommandTool,
])
def test_operational_guidance_reaches_both_model_protocols(tool_class) -> None:
    # Guidance is class-level. Rendering it needs no environment or filesystem.
    tool = object.__new__(tool_class)
    text = tool_contracts_text((tool,))
    native = native_tool_schemas((tool,))[0]["function"]

    assert tool.model_guidance.strip()
    assert f"- {tool.name}:" in text
    assert native["name"] == tool.name
    assert tool.model_guidance in text
    assert tool.model_guidance in native["description"]
