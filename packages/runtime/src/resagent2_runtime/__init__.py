"""The ResAgent2 shared Agentic Loop runtime (how an Agent runs).

This package owns the execution engine only: the Agentic Loop, Agent state,
context composition, LLM calls, session persistence and the Tool protocol plus
the loop's own finish/ask_user tools. Concrete abilities (filesystem, Git,
process, repository, environment, dataset, hardware) live in
``resagent2_components``. Model-facing wrappers live in
``resagent2_capabilities``; Agents choose tools through their Tool Profile.
"""

from .context import (
    DEFAULT_AGENT_CONTEXT_TOKENS,
    ContextMaterial,
    ContextBudgetExceeded,
    ContextComposer,
    recent_tool_listing,
    recent_tool_snippets,
)
from .llm import (
    LLMClient,
    LLMExhaustedError,
    ModelProfile,
    OpenAICompatibleClient,
    PromptLLMClient,
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
    NativeToolCall,
    PermissionDecision,
    ToolCallTurn,
    ToolObservation,
)
from .store import InMemorySessionStore, JsonSessionStore, SessionStore
from .tools import (
    AskUserTool,
    AskUserToolInput,
    FinishInput,
    FinishTool,
    Tool,
    ToolNotFoundError,
    ToolRegistry,
)

__all__ = [
    "DEFAULT_AGENT_CONTEXT_TOKENS",
    "ContextMaterial",
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
    "ModelProfile",
    "NativeToolCall",
    "OpenAICompatibleClient",
    "PermissionDecision",
    "PermissionPolicy",
    "PromptLLMClient",
    "recent_tool_listing",
    "recent_tool_snippets",
    "ScriptedLLMClient",
    "SessionStore",
    "Tool",
    "ToolCallTurn",
    "ToolNotFoundError",
    "ToolObservation",
    "ToolRegistry",
]
