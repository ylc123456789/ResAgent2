"""Production composition root for the standalone CLI.

This module only creates and connects existing ResAgent2 components. Research
control, task scheduling, Agent behavior, persistence semantics, and evidence
validation remain in their owning packages.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from resagent2_capabilities import ArxivLiteratureBackend, ResourceLayout
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
    ArtifactRegistry,
    JsonRunStore,
    LLMWorkflowCompiler,
    ModuleBinding,
    ResearchController,
    WorkflowScheduler,
)
from resagent2_runtime import (
    ComposedContext,
    JsonSessionStore,
    OpenAICompatibleClient,
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


def _client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        model=os.environ.get("RESAGENT2_MODEL", "deepseek-chat"),
        api_base=os.environ.get("RESAGENT2_API_BASE", "https://api.deepseek.com/v1"),
        api_key_env=os.environ.get("RESAGENT2_API_KEY_ENV", "DEEPSEEK_API_KEY"),
        trace_dir=_trace_dir(),
        trace_level=os.environ.get("RESAGENT2_LLM_TRACE_LEVEL", "off"),
    )


class _CompilerClient:
    """Bridge the runtime LLM client to the compiler's plain-prompt Port."""

    def __init__(self) -> None:
        self._client = _client()

    def set_trace_context(self, **kwargs) -> None:
        self._client.set_trace_context(**kwargs)

    def set_attempt_limit(self, max_attempts: int) -> None:
        self._client.set_attempt_limit(max_attempts)

    @property
    def last_attempts(self) -> int:
        return self._client.last_attempts

    def next_action(self, prompt: str, action_type):
        return self._client.next_action(
            ComposedContext(
                text=prompt,
                included_sections=[],
                omitted_sections=[],
                estimated_tokens=0,
            ),
            action_type,
        )


class _ScientificArtifactRegistration:
    """Freeze a Scientific Tool artifact and add it to the Run index."""

    def __init__(self, registry: ArtifactRegistry, store: JsonRunStore) -> None:
        self._registry = registry
        self._store = store
        self._live: dict = {}

    def register_scientific(self, candidate, *, run_id, session_id):
        artifact = self._registry.register_scientific(
            candidate,
            run_id=run_id,
            session_id=session_id,
        )
        run = self._store.load(run_id)
        run.artifacts[artifact.id] = artifact
        self._store.save(run)
        self._live[artifact.id] = artifact
        return artifact

    def resolve(self, artifact_id):
        return self._live.get(artifact_id)


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
                request_model="CodeUnderstandInput",
                result_model="CodeUnderstandResult",
                permission_policy="read_only_workspace",
                completion_evidence=[],
            ),
            CapabilityDefinition(
                capability=Capability.CODE_MODIFY,
                owner=AgentOwner.CODING,
                description=(
                    "Change code to implement a feature or fix a bug; it already "
                    "reads and diagnoses the code before editing."
                ),
                request_model="CodeModifyInput",
                result_model="CodeModifyResult",
                permission_policy="read_write_workspace",
                completion_evidence=["code_change"],
            ),
            CapabilityDefinition(
                capability=Capability.EXPERIMENT_RUN,
                owner=AgentOwner.EXPERIMENT,
                description=(
                    "Run an experiment and record its measured metrics and artifacts."
                ),
                request_model="ExperimentRunInput",
                result_model="ExperimentResult",
                permission_policy="read_write_workspace",
                completion_evidence=["experiment_result"],
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
    coding_store = JsonSessionStore(root / "sessions" / "coding")
    experiment_store = JsonSessionStore(root / "sessions" / "experiment")
    scientific_store = JsonSessionStore(root / "sessions" / "scientific")

    scheduler = WorkflowScheduler(
        bindings={
            Capability.CODE_UNDERSTAND: ModuleBinding(
                owner=_owner_for(registry, Capability.CODE_UNDERSTAND),
                port=NativeCodingAgent(_client(), store=coding_store),
            ),
            Capability.CODE_MODIFY: ModuleBinding(
                owner=_owner_for(registry, Capability.CODE_MODIFY),
                port=NativeCodingAgent(_client(), store=coding_store),
            ),
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=_owner_for(registry, Capability.EXPERIMENT_RUN),
                port=NativeExperimentAgent(
                    _client(),
                    store=experiment_store,
                    resource_layout=resource_layout,
                ),
            ),
        },
        store=run_store,
        artifact_root=root / "artifacts",
        data_root=root,
        workspaces=workspaces,
    )
    registration = _ScientificArtifactRegistration(
        scheduler.artifact_registry,
        run_store,
    )
    scientific = ScientificAgent(
        _client(),
        literature_backend=ArxivLiteratureBackend(),
        registration_port=registration,
        store=scientific_store,
    )
    controller = ResearchController(
        scientific_port=scientific,
        compiler=LLMWorkflowCompiler(_CompilerClient()),
        scheduler=scheduler,
        registry=registry,
    )
    return CliApplication(controller=controller, run_store=run_store)
