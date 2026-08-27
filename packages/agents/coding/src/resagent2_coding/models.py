"""Coding-specific action and finish-candidate schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from resagent2_runtime import AgentAction
from resagent2_runtime.models import NonEmptyStr, RuntimeModel


class CodeUnderstandAction(AgentAction):
    """Tool names available to the physically read-only Coding profile."""

    tool: Literal[
        "list_files",
        "read_file",
        "search_text",
        "read_artifact",
        "git_diff",
        "finish",
        "ask_user",
    ]


class CodeModifyAction(AgentAction):
    """Tool names available to the bounded code-modification profile."""

    tool: Literal[
        "list_files",
        "read_file",
        "search_text",
        "read_artifact",
        "git_diff",
        "create_file",
        "replace_text",
        "run_verification",
        "finish",
        "ask_user",
    ]


class CodeModifyFinish(RuntimeModel):
    summary: NonEmptyStr
    residual_risks: list[NonEmptyStr] = Field(default_factory=list)
