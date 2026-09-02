"""Tests for the model-facing per-tool argument contract."""

from pydantic import BaseModel

from resagent2_runtime import AgentAction
from resagent2_runtime.llm import OpenAICompatibleClient
from resagent2_runtime.tools import tool_contracts_text


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


def test_action_instruction_appends_contracts() -> None:
    contracts = tool_contracts_text((_FinishTool,))

    instruction = OpenAICompatibleClient._action_instruction(AgentAction, contracts)

    assert contracts in instruction
    assert "Do not use markdown fences" in instruction


def test_agent_action_has_no_reasoning_summary() -> None:
    # The dead field must be gone so it no longer competes with a tool's
    # required `summary` argument.
    assert "reasoning_summary" not in AgentAction.model_fields
