"""The ResAgent2 shared Agentic Loop runtime (how an Agent runs).

This package owns the execution engine only: the Agentic Loop, Agent state,
context composition, LLM calls, session persistence and the Tool protocol plus
the loop's own finish/ask_user tools. Concrete abilities (filesystem, Git,
process, repository, environment, dataset, hardware) live in
``resagent2_capabilities``, which Agents assemble through their Tool Profile.
"""

from .context import ContextBudgetExceeded, ContextComposer
from .llm import (
    LLMClient,
    LLMExhaustedError,
    OpenAICompatibleClient,
    ScriptedLLMClient,
)
from .loop import (
    AgentDefinition,
    AgentLoop,
    AllowListPermissionPolicy,
    CompletionCheck,
    ContextBuilder,
    PermissionPolicy,
)
from .models import (
    AgentAction,
    AgentEvent,
    AgentState,
    CompletionDecision,
    ComposedContext,
    ContextSection,
    FinishCandidate,
    PermissionDecision,
    ToolObservation,
)
from .store import InMemorySessionStore, JsonSessionStore, SessionStore
from .tools import (
    AskUserTool,
    AskUserToolInput,
    FinishInput,
    FinishTool,
    ReadValueInput,
    ReadValueTool,
    Tool,
    ToolNotFoundError,
    ToolRegistry,
    WriteValueInput,
    WriteValueTool,
)

__all__ = [
    "AgentAction",
    "AgentDefinition",
    "AgentEvent",
    "AgentLoop",
    "AgentState",
    "AllowListPermissionPolicy",
    "AskUserTool",
    "AskUserToolInput",
    "CompletionCheck",
    "CompletionDecision",
    "ComposedContext",
    "ContextBudgetExceeded",
    "ContextBuilder",
    "ContextComposer",
    "ContextSection",
    "FinishCandidate",
    "FinishInput",
    "FinishTool",
    "InMemorySessionStore",
    "JsonSessionStore",
    "LLMClient",
    "LLMExhaustedError",
    "OpenAICompatibleClient",
    "PermissionDecision",
    "PermissionPolicy",
    "ReadValueInput",
    "ReadValueTool",
    "ScriptedLLMClient",
    "SessionStore",
    "Tool",
    "ToolNotFoundError",
    "ToolObservation",
    "ToolRegistry",
    "WriteValueInput",
    "WriteValueTool",
]
