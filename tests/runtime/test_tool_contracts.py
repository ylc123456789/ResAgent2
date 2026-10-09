"""Tests for the model-facing per-tool argument contract."""

import pytest

from pydantic import BaseModel

from resagent2_capabilities import (
    AuditEnvTool, PrepareEnvironmentTool, ReplaceTextTool, RunSetupTool,
)

from resagent2_coding.verification import RunVerificationTool
from resagent2_capabilities import RunShellTool
from resagent2_runtime import AgentAction, AskUserTool, FinishTool
from resagent2_scientific.tools import (
    AskUserTool as ScientificAskUserTool, RequestWorkTool,
)
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


def test_native_schemas_list_required_arguments() -> None:
    schemas = native_tool_schemas((_FinishTool, _AskTool))
    assert schemas[0]["function"]["parameters"]["required"] == ["opinion", "summary"]
    assert schemas[1]["function"]["parameters"]["required"] == ["text", "reason"]


def test_agent_action_has_no_reasoning_summary() -> None:
    # The dead field must be gone so it no longer competes with a tool's
    # required `summary` argument.
    assert "reasoning_summary" not in AgentAction.model_fields


def test_ask_user_contract_requires_requested_fields() -> None:
    from resagent2_runtime import AskUserTool

    required = native_tool_schemas((AskUserTool(),))[0]["function"]["parameters"]["required"]
    assert required == ["text", "requested_fields"]


def test_tool_contracts_include_optional_model_guidance() -> None:
    class _ReadInput(BaseModel):
        path: str

    class _ReadTool:
        name = "read_file"
        input_model = _ReadInput
        model_guidance = "read a bounded start_line/end_line range when truncated"

    schemas = native_tool_schemas((_ReadTool, _FinishTool))
    assert schemas[0]["function"]["parameters"]["required"] == ["path"]
    assert _ReadTool.model_guidance in schemas[0]["function"]["description"]
    assert schemas[1]["function"]["parameters"]["required"] == ["opinion", "summary"]


@pytest.mark.parametrize("tool_class", [
    PrepareEnvironmentTool, AuditEnvTool, RunSetupTool, ReplaceTextTool,
    RequestWorkTool, ScientificAskUserTool, FinishTool, AskUserTool,
    RunVerificationTool, RunShellTool,
])
def test_operational_guidance_reaches_native_tool_schema(tool_class) -> None:
    # Guidance is class-level. Rendering it needs no environment or filesystem.
    tool = object.__new__(tool_class)
    native = native_tool_schemas((tool,))[0]["function"]

    assert tool.model_guidance.strip()
    assert native["name"] == tool.name
    assert tool.model_guidance in native["description"]
