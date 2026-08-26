"""Typed in-memory Tools and their small dispatch registry."""

from __future__ import annotations

from typing import Protocol, cast

from pydantic import BaseModel, Field, JsonValue

from resagent2_contracts import QuestionDraft

from .models import AgentState, FinishCandidate, NonEmptyStr, RuntimeModel, ToolObservation


class Tool(Protocol):
    """Protocol implemented by every runtime Tool."""

    name: str
    input_model: type[BaseModel]

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        """Execute validated arguments without mutating state directly."""


class ToolNotFoundError(LookupError):
    """Raised when an action names a Tool outside its Agent definition."""


class ToolRegistry:
    """Validates Tool names and argument schemas before dispatch."""

    def __init__(self, tools: tuple[Tool, ...]) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"duplicate tool name: {tool.name}")
            self._tools[tool.name] = tool

    def contains(self, name: str) -> bool:
        """Return whether a Tool name belongs to this registry."""

        return name in self._tools

    def dispatch(
        self,
        name: str,
        arguments: dict[str, JsonValue],
        state: AgentState,
    ) -> ToolObservation:
        """Validate raw arguments and execute the selected Tool."""

        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(name)
        parsed = tool.input_model.model_validate(arguments)
        return tool.execute(state, parsed)


class ReadValueInput(RuntimeModel):
    """Input schema for ReadValueTool."""

    key: NonEmptyStr


class ReadValueTool:
    """Read one key from generic in-memory Agent state."""

    name = "read_value"
    input_model = ReadValueInput

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(ReadValueInput, arguments)
        return ToolObservation(
            summary=f"Read memory key {args.key!r}",
            value=state.memory.get(args.key),
        )


class WriteValueInput(RuntimeModel):
    """Input schema for WriteValueTool."""

    key: NonEmptyStr
    value: JsonValue


class WriteValueTool:
    """Propose one generic in-memory state update."""

    name = "write_value"
    input_model = WriteValueInput

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(WriteValueInput, arguments)
        return ToolObservation(
            summary=f"Wrote memory key {args.key!r}",
            value=args.value,
            memory_updates={args.key: args.value},
        )


class FinishInput(RuntimeModel):
    """Input schema for FinishTool."""

    proposed_status: NonEmptyStr = "completed"
    result: JsonValue
    artifact_paths: list[NonEmptyStr] = Field(default_factory=list)
    unresolved_items: list[NonEmptyStr] = Field(default_factory=list)


class FinishTool:
    """Create a finish candidate without deciding the actual ModuleStatus."""

    name = "finish"
    input_model = FinishInput

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(FinishInput, arguments)
        return ToolObservation(
            summary="Produced a finish candidate",
            finish_candidate=FinishCandidate(**args.model_dump()),
        )


class AskUserToolInput(RuntimeModel):
    """Input schema for AskUserTool."""

    text: NonEmptyStr
    requested_fields: list[NonEmptyStr] = Field(default_factory=list)
    reason: NonEmptyStr


class AskUserTool:
    """Return a question signal without performing terminal or UI I/O."""

    name = "ask_user"
    input_model = AskUserToolInput

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(AskUserToolInput, arguments)
        return ToolObservation(
            summary="User input is required",
            question=QuestionDraft(**args.model_dump()),
        )
