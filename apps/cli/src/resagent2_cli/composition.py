"""Production composition root for the standalone CLI.

This module only creates and connects existing ResAgent2 components. Research
control, task scheduling, Agent behavior, persistence semantics, and evidence
validation remain in their owning packages.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from resagent2_components import (
    ArxivLiteratureBackend,
    MultiSourceLiteratureBackend,
    OpenAlexLiteratureBackend,
    DeepSeekWebSearchBackend,
    TavilyWebSearchBackend,
    WebPageFetcher,
)
from resagent2_components import (
    DatasetCatalog,
    ResourceLayout,
)
from resagent2_components.literature.fulltext import DEFAULT_PDF_PARSE_TIMEOUT_SECONDS, parse_pdf
from resagent2_coding import NativeCodingAgent
from resagent2_contracts import (
    AgentOwner,
    WorkflowAgentKind,
    WorkflowAgentDefinition,
    WorkflowAgentRegistry,
    WorkspaceSpec,
)
from resagent2_experiment import NativeExperimentAgent
from resagent2_orchestrator import (
    JsonRunStore,
    LLMWorkflowCompiler,
    DeterministicWorkInterpreter,
    ModuleBinding,
    ResearchController,
    ScientificArtifactRegistration,
    WorkflowScheduler,
)
from resagent2_runtime import (
    DEFAULT_AGENT_CONTEXT_TOKENS,
    JsonSessionStore,
    ModelProfile,
    ModelRequestClient,
    OpenAICompatibleClient,
    PromptLLMClient,
)
from resagent2_scientific import ScientificAgent


@dataclass(frozen=True, slots=True)
class CliApplication:
    """Objects the CLI needs after composition."""

    controller: ResearchController
    run_store: JsonRunStore


def _trace_dir() -> Path | None:
    value = os.environ.get("RESAGENT2_LLM_TRACE_DIR")
    return Path(value).expanduser().resolve() if value else None


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _web_search_provider() -> str | None:
    """Validate explicit provider credentials before creating clients or files."""

    explicit_provider = os.environ.get("RESAGENT2_WEB_SEARCH_PROVIDER")
    provider = "deepseek" if explicit_provider is None else explicit_provider
    if provider not in {"deepseek", "tavily", "off"}:
        raise ValueError("RESAGENT2_WEB_SEARCH_PROVIDER must be deepseek, tavily, or off")
    if provider == "off":
        return None
    key_env = "DEEPSEEK_API_KEY" if provider == "deepseek" else "TAVILY_API_KEY"
    if not os.environ.get(key_env, "").strip():
        if explicit_provider is not None:
            raise ValueError(f"RESAGENT2_WEB_SEARCH_PROVIDER={provider} requires {key_env}")
        return None
    return provider


def _model_profile() -> ModelProfile:
    """Declare model capacity separately from module input allowances."""

    return ModelProfile(
        context_window=_positive_int_env("RESAGENT2_CONTEXT_WINDOW", 1_000_000),
        reserved_output_tokens=_positive_int_env(
            "RESAGENT2_RESERVED_OUTPUT_TOKENS", 256_000
        ),
        safety_margin_tokens=_positive_int_env(
            "RESAGENT2_CONTEXT_SAFETY_MARGIN_TOKENS", 1024
        ),
    )


def _component_context_limit(component: str) -> int:
    return _positive_int_env(
        f"RESAGENT2_{component.upper()}_CONTEXT_TOKENS",
        DEFAULT_AGENT_CONTEXT_TOKENS,
    )


def _client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        model=os.environ.get("RESAGENT2_MODEL", "deepseek-v4-flash"),
        api_base=os.environ.get("RESAGENT2_API_BASE", "https://api.deepseek.com/v1"),
        api_key_env=os.environ.get("RESAGENT2_API_KEY_ENV", "DEEPSEEK_API_KEY"),
        model_profile=_model_profile(),
        timeout_seconds=_positive_int_env("RESAGENT2_LLM_TIMEOUT_SECONDS", 600),
        trace_dir=_trace_dir(),
        trace_level=os.environ.get("RESAGENT2_LLM_TRACE_LEVEL", "off"),
    )


def _compiler_client(*, max_context_tokens: int) -> PromptLLMClient:
    return PromptLLMClient(
        _client(),
        system_prompt="You are the stateless ResAgent2 Workflow Compiler.",
        max_context_tokens=max_context_tokens,
        section_name="compiler_request",
    )


def _registry() -> WorkflowAgentRegistry:
    return WorkflowAgentRegistry(
        definitions=[
            WorkflowAgentDefinition(
                workflow_agent_kind=WorkflowAgentKind.CODING,
                description=NativeCodingAgent.description,
            ),
            WorkflowAgentDefinition(
                workflow_agent_kind=WorkflowAgentKind.EXPERIMENT,
                description=NativeExperimentAgent.description,
            ),
        ]
    )


def build_application(
    *,
    data_root: str | Path,
    workspaces: dict[str, WorkspaceSpec] | None = None,
) -> CliApplication:
    """Create the existing system behind the CLI boundary."""

    pdf_parse_timeout_seconds = _positive_int_env(
        "RESAGENT2_PDF_PARSE_TIMEOUT_SECONDS", DEFAULT_PDF_PARSE_TIMEOUT_SECONDS,
    )
    web_timeout_seconds = _positive_int_env(
        "RESAGENT2_WEB_TIMEOUT_SECONDS", 30,
    )
    web_max_response_bytes = _positive_int_env(
        "RESAGENT2_WEB_MAX_RESPONSE_BYTES", 4 * 1024 * 1024,
    )
    web_search_timeout_seconds = _positive_int_env(
        "RESAGENT2_WEB_SEARCH_TIMEOUT_SECONDS", 60,
    )
    web_search_provider = _web_search_provider()
    root = Path(data_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    registry = _registry()
    run_store = JsonRunStore(root / "state")
    resource_layout = ResourceLayout.from_env(data_root=root)
    dataset_catalog = DatasetCatalog(resource_layout.dataset_root)
    coding_store = JsonSessionStore(root / "sessions" / "coding")
    experiment_store = JsonSessionStore(root / "sessions" / "experiment")
    scientific_store = JsonSessionStore(root / "sessions" / "scientific")
    coding_context_tokens = _component_context_limit("coding")
    experiment_context_tokens = _component_context_limit("experiment")
    scientific_context_tokens = _component_context_limit("scientific")
    compiler_context_tokens = _component_context_limit("compiler")

    scheduler = WorkflowScheduler(
        bindings={
            WorkflowAgentKind.CODING: ModuleBinding(
                owner=AgentOwner.CODING,
                port=NativeCodingAgent(
                    _client(),
                    store=coding_store,
                    resource_layout=resource_layout,
                    max_context_tokens=coding_context_tokens,
                ),
            ),
            WorkflowAgentKind.EXPERIMENT: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=NativeExperimentAgent(
                    _client(),
                    store=experiment_store,
                    resource_layout=resource_layout,
                    max_context_tokens=experiment_context_tokens,
                ),
            ),
        },
        store=run_store,
        artifact_root=root / "artifacts",
        data_root=root,
        workspaces=workspaces,
    )
    registration = ScientificArtifactRegistration(
        scheduler.artifact_registry,
        run_store,
    )
    web_search_backend = None
    if web_search_provider == "deepseek":
        web_search_backend = DeepSeekWebSearchBackend(
            ModelRequestClient(
                endpoint="https://api.deepseek.com/anthropic/v1/messages",
                api_key_env="DEEPSEEK_API_KEY",
                timeout_seconds=web_search_timeout_seconds,
                max_response_bytes=web_max_response_bytes,
                extra_headers={"anthropic-version": "2023-06-01"},
                api_key_headers=("x-api-key", "Authorization"),
                trace_dir=_trace_dir(),
                trace_level=os.environ.get("RESAGENT2_LLM_TRACE_LEVEL", "off"),
            ),
            model=os.environ.get("RESAGENT2_WEB_SEARCH_MODEL", "deepseek-flash"),
        )
    elif web_search_provider == "tavily":
        web_search_backend = TavilyWebSearchBackend(
            os.environ["TAVILY_API_KEY"],
            timeout_seconds=web_timeout_seconds,
            max_response_bytes=web_max_response_bytes,
        )
    scientific = ScientificAgent(
        _client(),
        literature_backend=MultiSourceLiteratureBackend(
            ArxivLiteratureBackend(),
            OpenAlexLiteratureBackend(api_key=os.environ.get("OPENALEX_API_KEY")),
        ),
        literature_parser=partial(parse_pdf, timeout_seconds=pdf_parse_timeout_seconds),
        web_search_backend=web_search_backend,
        web_page_fetcher=WebPageFetcher(
            timeout_seconds=web_timeout_seconds,
            max_response_bytes=web_max_response_bytes,
        ),
        registration_port=registration,
        store=scientific_store,
        max_context_tokens=scientific_context_tokens,
        resource_layout=resource_layout,
    )
    controller = ResearchController(
        scientific_port=scientific,
        compiler=LLMWorkflowCompiler(
            _compiler_client(max_context_tokens=compiler_context_tokens)
        ),
        interpreter=DeterministicWorkInterpreter(),
        scheduler=scheduler,
        registry=registry,
        dataset_ref_source=dataset_catalog,
    )
    trace_level = os.environ.get("RESAGENT2_LLM_TRACE_LEVEL", "off")
    print(f"LLM trace: {trace_level}; directory: {_trace_dir() or 'unset'}", file=sys.stderr)
    return CliApplication(controller=controller, run_store=run_store)
