"""Experiment-specific action and finish-candidate schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from resagent2_runtime import AgentAction
from resagent2_runtime.models import NonEmptyStr, RuntimeModel


class ExperimentAction(AgentAction):
    """Tool names available to the native Experiment Agent."""

    tool: Literal[
        "list_files",
        "read_file",
        "search_text",
        "read_artifact",
        "prepare_environment",
        "run_setup",
        "audit_env",
        "run_command",
        "finish",
        "ask_user",
    ]


class ExperimentFinish(RuntimeModel):
    """LLM-proposed experiment result; the finalizer derives the real facts.

    The Agent reports only summary, evidence_files and residual_risks; typed
    metrics are derived by the deterministic finalizer from JSON evidence, not
    supplied by the model (ADR-0011 §5.2).
    """

    summary: NonEmptyStr
    evidence_files: list[NonEmptyStr] = Field(default_factory=list)
    residual_risks: list[NonEmptyStr] = Field(default_factory=list)
