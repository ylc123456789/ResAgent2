"""Production composition root for the standalone CLI.

This module only creates and connects existing ResAgent2 components. Research
control, task scheduling, Agent behavior, persistence semantics, and evidence
validation remain in their owning packages.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from resagent2_capabilities import (
    ArxivLiteratureBackend,
    DatasetCatalog,
    ResourceLayout,
)
from resagent2_coding import NativeCodingAgent
from resagent2_contracts import (
    AgentOwner,
    Capability,
    CapabilityDefinition,
    CapabilityRegistry,
    WorkspaceSpec,
)
from resagent2_experiment import NativeExperimentAgent
from resagent2_orchestrator import (
    JsonRunStore,
    LLMWorkflowCompiler,
    ModuleBinding,
    ResearchController,
    ScientificArtifactRegistration,
    WorkflowScheduler,
)
from resagent2_runtime import (
    JsonSessionStore,
    ModelProfile,
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
    return Path(value).expanduser() if value else None


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _model_profile() -> ModelProfile:
    """Deployment defaults match V4 capacity; module input policies stay small."""

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
    # Agents retain evidence snippets plus tool contracts; Compiler has no reads.
    default = 8192 if component in {"scientific", "coding", "experiment"} else 4096
    return _positive_int_env(
        f"RESAGENT2_{component.upper()}_CONTEXT_TOKENS",
        default,
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


def _registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        definitions=[
            CapabilityDefinition(
                capability=Capability.CODE_UNDERSTAND,
                owner=AgentOwner.CODING,
                description=(
                    "Read-only code inspection; use only when the goal is to "
                    "analyze or explain code without changing it."
                ),
            ),
            CapabilityDefinition(
                capability=Capability.CODE_MODIFY,
                owner=AgentOwner.CODING,
                description=(
                    "Change code to implement a feature or fix a bug; it already "
                    "reads and diagnoses the code before editing."
                ),
            ),
            CapabilityDefinition(
                capability=Capability.EXPERIMENT_RUN,
                owner=AgentOwner.EXPERIMENT,
                description=(
                    "Run an experiment and record its measured metrics and artifacts."
                ),
            ),
        ]
    )


def _owner_for(registry: CapabilityRegistry, capability: Capability) -> AgentOwner:
    for definition in registry.definitions:
        if definition.capability == capability:
            return definition.owner
    raise KeyError(f"no owner registered for capability {capability.value}")


def build_application(
    *,
    data_root: str | Path,
    workspaces: dict[str, WorkspaceSpec] | None = None,
) -> CliApplication:
    """Create the existing system behind the CLI boundary."""

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
            Capability.CODE_UNDERSTAND: ModuleBinding(
                owner=_owner_for(registry, Capability.CODE_UNDERSTAND),
                port=NativeCodingAgent(
                    _client(),
                    store=coding_store,
                    resource_layout=resource_layout,
                    max_context_tokens=coding_context_tokens,
                ),
            ),
            Capability.CODE_MODIFY: ModuleBinding(
                owner=_owner_for(registry, Capability.CODE_MODIFY),
                port=NativeCodingAgent(
                    _client(),
                    store=coding_store,
                    resource_layout=resource_layout,
                    max_context_tokens=coding_context_tokens,
                ),
            ),
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=_owner_for(registry, Capability.EXPERIMENT_RUN),
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
    scientific = ScientificAgent(
        _client(),
        literature_backend=ArxivLiteratureBackend(),
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
        scheduler=scheduler,
        registry=registry,
        dataset_ref_source=dataset_catalog,
    )
    return CliApplication(controller=controller, run_store=run_store)
