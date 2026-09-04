"""Native Experiment Agent ModulePort built on the shared AgentLoop."""

from __future__ import annotations

from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    Capability,
    ErrorCode,
    ExperimentResult,
    ExperimentRunInput,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    ModuleTaskRequest,
    WorkspaceMode,
    WorkspaceSourceKind,
    WorkspaceSpec,
)
from resagent2_capabilities import (
    AuditEnvTool,
    DatasetResolutionError,
    dataset_env_overrides,
    EnvironmentBinding,
    EnvironmentManager,
    HardwareAudit,
    ListFilesTool,
    PrepareEnvironmentTool,
    ProcessRunner,
    ReadArtifactTool,
    ReadFileTool,
    RegisteredArtifactReader,
    RepoMaterializer,
    RepoMaterializerError,
    ResourceLayout,
    RunSetupTool,
    SearchTextTool,
    resolve_dataset_refs,
    WorkspaceBoundary,
    WorkspaceObserver,
    WorkspacePermissionError,
)
from resagent2_runtime import (
    AgentDefinition,
    AgentLoop,
    AllowListPermissionPolicy,
    AskUserTool,
    FinishTool,
    InMemorySessionStore,
    LLMClient,
    SessionStore,
)

from .completion import ExperimentCompletionCheck
from .context import EXPERIMENT_PROMPT, build_context
from .models import ExperimentAction
from .tools import RunCommandTool


def _confirmation_granted(request: ModuleTaskRequest, confirm_before_experiment: bool) -> bool:
    if not confirm_before_experiment:
        return True
    for answer in request.answers:
        value = (answer.values.get("approve") or "").strip().lower()
        if value in {"yes", "y", "true", "1", "approve", "ok", "confirm"}:
            return True
    return False


class NativeExperimentAgent:
    """Implement experiment_run with repo provisioning and delivery validation."""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        store: SessionStore | None = None,
        resource_layout: ResourceLayout | None = None,
        max_context_tokens: int = 4096,
    ) -> None:
        if max_context_tokens < 1:
            raise ValueError("max_context_tokens must be positive")
        self.llm_client = llm_client
        self.loop = AgentLoop(store=store or InMemorySessionStore())
        self.resource_layout = resource_layout or ResourceLayout.from_env()
        self.max_context_tokens = max_context_tokens

    @staticmethod
    def _failure(message: str, *, blocked: bool = False) -> ModuleResult:
        error = ModuleError(
            code=ErrorCode.INVALID_INPUT,
            message=message,
            retryable=False,
        )
        return ModuleResult(
            status=ModuleStatus.BLOCKED if blocked else ModuleStatus.FAILED,
            summary=message,
            error=error,
        )

    def invoke(self, request: ModuleTaskRequest) -> ModuleResult:
        if request.capability != Capability.EXPERIMENT_RUN:
            return self._failure("NativeExperimentAgent received a non-Experiment capability")
        if request.workspace is None or request.workspace.mode != WorkspaceMode.READ_WRITE:
            return self._failure("experiment_run requires a read_write workspace", blocked=True)
        if request.output_dir is None:
            return self._failure("ExperimentAgent requires an output_dir", blocked=True)

        inputs = request.inputs  # ExperimentRunInput
        spec = request.workspace_spec
        if spec is None:
            spec = WorkspaceSpec(
                workspace_id=request.workspace_id or "workspace",
                source_kind=WorkspaceSourceKind.LOCAL,
                location=str(Path(request.workspace.root).expanduser().resolve()),
            )
        try:
            materialized = RepoMaterializer().materialize(
                workspace=Path(request.workspace.root),
                source=spec,
            )
        except RepoMaterializerError as error:
            return self._failure(str(error), blocked=True)

        try:
            boundary = WorkspaceBoundary(request.workspace)
        except WorkspacePermissionError as error:
            return self._failure(str(error), blocked=True)
        if materialized.repo_path.resolve() != boundary.root.resolve():
            return self._failure(
                "materialized repository is not the granted workspace root", blocked=True
            )
        observer = WorkspaceObserver(boundary)

        source_ref = (
            spec.location if spec.location else str(materialized.repo_path)
        )
        resource_layout = self.resource_layout
        try:
            datasets = resolve_dataset_refs(
                resource_layout.dataset_root, list(request.dataset_refs)
            )
        except DatasetResolutionError as error:
            return self._failure(str(error), blocked=True)

        workspace_id = request.workspace_id or (
            request.workspace_spec.workspace_id if request.workspace_spec else None
        )
        if workspace_id is None:
            return self._failure("experiment_run requires a workspace_id", blocked=True)
        manager = EnvironmentManager(env_root=resource_layout.env_root)
        binding = EnvironmentBinding(
            manager,
            run_id=request.run_id,
            workspace_id=workspace_id,
            hard_constraint=request.environment_spec.python_version,
        )
        env_id = manager.env_id(run_id=request.run_id, workspace_id=workspace_id)

        confirmed = _confirmation_granted(request, inputs.confirm_before_experiment)
        dataset_env = dataset_env_overrides(resource_layout.dataset_root, datasets)

        output_dir = request.output_dir
        command_log_dir = f"{output_dir}/commands"
        setup_log_dir = f"{output_dir}/setup"

        runner = ProcessRunner(boundary)
        tools = (
            ListFilesTool(boundary),
            ReadFileTool(boundary),
            SearchTextTool(boundary),
            ReadArtifactTool(RegisteredArtifactReader(request.input_artifacts)),
            PrepareEnvironmentTool(binding),
            RunSetupTool(
                runner,
                binding,
                log_dir=setup_log_dir,
                timeout_seconds=request.budget.timeout_seconds,
            ),
            AuditEnvTool(binding),
            RunCommandTool(
                runner,
                binding,
                confirm_before_experiment=inputs.confirm_before_experiment,
                confirmed=confirmed,
                timeout_seconds=request.budget.timeout_seconds,
                extra_env=dataset_env,
                log_dir=command_log_dir,
            ),
            AskUserTool(),
            FinishTool(),
        )
        definition = AgentDefinition(
            name="experiment-run",
            owner=AgentOwner.EXPERIMENT,
            system_prompt=EXPERIMENT_PROMPT,
            tools=tools,
            llm_client=self.llm_client,
            context_builder=build_context,
            permission_policy=AllowListPermissionPolicy({tool.name for tool in tools}),
            completion_check=ExperimentCompletionCheck(
                observer,
                expected_metrics=list(inputs.expected_metrics),
                expected_artifacts=list(inputs.expected_artifacts),
                env_id=env_id,
                repo_url=source_ref,
                commit=materialized.commit,
            ),
            action_type=ExperimentAction,
            result_type=ExperimentResult,
            max_context_tokens=self.max_context_tokens,
        )
        initial_memory = {
            "repo": {"repo_url": source_ref, "commit": materialized.commit},
            "hardware": HardwareAudit().text(),
            "command_count": 0,
            "experiment_success_count": 0,
        }
        if request.parent_session_id is None:
            # The Attempt-start baseline is persisted once; on resume the loop
            # reloads it from Session memory instead of re-snapshotting, so a
            # pre-pause command's output is not mistaken for the starting state
            # (ADR-0011 §2).
            initial_memory["workspace_snapshot"] = observer.snapshot().to_memory()
        return self.loop.run(
            definition,
            request,
            session_id=f"session_{request.task_id}_{request.attempt_number}",
            initial_memory=initial_memory,
        )
