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
    DatasetCache,
    DatasetResolutionError,
    dataset_env_overrides,
    EnvironmentManager,
    EnvironmentManagerError,
    HardwareAudit,
    ListFilesTool,
    ProcessRunner,
    ReadArtifactTool,
    ReadFileTool,
    RegisteredArtifactReader,
    RepoMaterializer,
    RepoMaterializerError,
    ResourceLayout,
    SearchTextTool,
    resolve_dataset_refs,
    WorkspaceBoundary,
    WorkspacePermissionError,
    env_id,
    env_spec,
    find_conda,
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

from .completion import ExperimentCompletionCheck, snapshot_workspace
from .context import EXPERIMENT_PROMPT, build_context
from .models import ExperimentAction
from .tools import AuditEnvTool, RunCommandTool


def _confirmation_granted(request: ModuleTaskRequest, confirm_before_experiment: bool) -> bool:
    if not confirm_before_experiment:
        return True
    for answer in request.answers:
        value = (answer.values.get("approve") or "").strip().lower()
        if value in {"yes", "y", "true", "1", "approve", "ok", "confirm"}:
            return True
    return False


class NativeExperimentAgent:
    """Implement experiment_run with repo/env provisioning and delivery validation."""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        store: SessionStore | None = None,
        resource_layout: ResourceLayout | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.loop = AgentLoop(store=store or InMemorySessionStore())
        self.resource_layout = resource_layout or ResourceLayout.from_env()

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

        source_ref = (
            spec.location if spec.location else str(materialized.repo_path)
        )
        resource_layout = self.resource_layout
        try:
            datasets = resolve_dataset_refs(
                resource_layout.dataset_root, list(inputs.dataset_refs)
            )
        except DatasetResolutionError as error:
            return self._failure(str(error), blocked=True)
        env_spec_dict = env_spec(materialized.repo_path, inputs.python_version)
        identifier = env_id(source_ref, f"{source_ref}\0{materialized.commit}", env_spec_dict)
        try:
            env_prefix = EnvironmentManager(env_root=resource_layout.env_root).ensure(
                identifier=identifier,
                repo_path=materialized.repo_path,
                python_version=inputs.python_version,
            )
        except EnvironmentManagerError as error:
            return self._failure(str(error), blocked=True)

        conda = find_conda()
        if conda is None:
            return self._failure("conda not found; set RESAGENT2_CONDA_EXE", blocked=True)
        argv_prefix = [conda, "run", "--no-capture-output", "-p", str(env_prefix)]

        confirmed = _confirmation_granted(request, inputs.confirm_before_experiment)
        dataset_env = DatasetCache(root=resource_layout.dataset_root).env_overrides()
        dataset_env.update(
            dataset_env_overrides(resource_layout.dataset_root, datasets)
        )

        output_dir = request.output_dir
        command_log_dir = f"{output_dir}/commands"
        audit_log_dir = f"{output_dir}/audit"
        probe_dir = output_dir

        runner = ProcessRunner(boundary)
        tools = (
            ListFilesTool(boundary),
            ReadFileTool(boundary),
            SearchTextTool(boundary),
            ReadArtifactTool(RegisteredArtifactReader(request.input_artifacts)),
            RunCommandTool(
                runner,
                argv_prefix=argv_prefix,
                env_prefix=env_prefix,
                confirm_before_experiment=inputs.confirm_before_experiment,
                confirmed=confirmed,
                timeout_seconds=request.budget.timeout_seconds,
                extra_env=dataset_env,
                log_dir=command_log_dir,
            ),
            AuditEnvTool(
                runner,
                boundary,
                argv_prefix=argv_prefix,
                env_prefix=env_prefix,
                timeout_seconds=min(request.budget.timeout_seconds, 180),
                extra_env=dataset_env,
                log_dir=audit_log_dir,
                probe_dir=probe_dir,
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
                boundary,
                expected_metrics=list(inputs.expected_metrics),
                expected_artifacts=list(inputs.expected_artifacts),
                env_id=identifier,
                repo_url=source_ref,
                commit=materialized.commit,
            ),
            action_type=ExperimentAction,
            result_type=ExperimentResult,
        )
        initial_memory = {
            "environment": {"env_id": identifier, "env_prefix": str(env_prefix)},
            "repo": {"repo_url": source_ref, "commit": materialized.commit},
            "datasets": datasets,
            "hardware": HardwareAudit().text(),
            "command_count": 0,
            "env_certified": False,
            "experiment_success_count": 0,
            "workspace_baseline": snapshot_workspace(boundary),
        }
        return self.loop.run(
            definition,
            request,
            session_id=f"session_{request.task_id}_{request.attempt_number}",
            initial_memory=initial_memory,
        )
