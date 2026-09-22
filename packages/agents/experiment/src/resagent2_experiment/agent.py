"""Native Experiment Agent with one invocation and completion protocol."""

from __future__ import annotations

from pathlib import Path

from resagent2_contracts import (
    AgentOwner, AgentRequest, AgentResult, ErrorCode, ModuleError, ModuleStatus,
    task_session_id,
)
from resagent2_capabilities import (
    AuditEnvTool, ListFilesTool, PrepareEnvironmentTool, ReadArtifactTool,
    ReadFileTool, RunSetupTool, SearchTextTool,
)
from resagent2_components import (
    ArtifactReadError, DatasetResolutionError, EnvironmentBinding,
    EnvironmentManager, ProcessRunner, RegisteredArtifactReader,
    RepoMaterializer, RepoMaterializerError, ResourceLayout,
    WorkspaceBoundary, WorkspaceObserver, WorkspacePermissionError,
    dataset_env_overrides, request_dataset_refs, resolve_dataset_refs,
)
from resagent2_runtime import (
    DEFAULT_AGENT_CONTEXT_TOKENS, AgentDefinition, AgentLoop,
    AskUserTool, FinishTool, InMemorySessionStore,
    LLMClient, SessionStore,
)

from .completion import ExperimentCompletionCheck
from .context import EXPERIMENT_PROMPT, build_context
from .models import ExperimentAction
from .tools import RunCommandTool
from resagent2_components.permissions import OperationPermissionPolicy
from resagent2_runtime.budget import DeadlineExceededError, execution_budget


class NativeExperimentAgent:
    """Analyze results and execute authorized experiments through one protocol."""

    def __init__(
        self, llm_client: LLMClient, *, store: SessionStore | None = None,
        resource_layout: ResourceLayout | None = None,
        max_context_tokens: int = DEFAULT_AGENT_CONTEXT_TOKENS,
    ) -> None:
        if max_context_tokens < 1:
            raise ValueError("max_context_tokens must be positive")
        self.llm_client = llm_client
        self.loop = AgentLoop(store=store or InMemorySessionStore())
        self.resource_layout = resource_layout or ResourceLayout.from_env()
        self.max_context_tokens = max_context_tokens

    @staticmethod
    def _failure(message: str, *, blocked: bool = False) -> AgentResult:
        return AgentResult(
            status=ModuleStatus.BLOCKED if blocked else ModuleStatus.FAILED,
            report=message,
            error=ModuleError(code=ErrorCode.INVALID_INPUT, message=message, retryable=False),
        )

    def invoke(self, request: AgentRequest) -> AgentResult:
        try:
            request = AgentRequest.model_validate(request)
        except ValueError as error:
            return self._failure(str(error))
        with execution_budget(max_llm_calls=request.budget.max_llm_calls,
                              timeout_seconds=request.budget.timeout_seconds):
            try:
                return self._invoke(request)
            except DeadlineExceededError as error:
                return AgentResult(status=ModuleStatus.FAILED, report=str(error),
                                   error=ModuleError(code=ErrorCode.TIMEOUT, message=str(error), retryable=False))

    def _invoke(self, request: AgentRequest) -> AgentResult:
        if request.agent != AgentOwner.EXPERIMENT:
            return self._failure("NativeExperimentAgent received a non-Experiment request")
        if request.workspace is None:
            return self._failure("Experiment requires a WorkspaceGrant", blocked=True)
        try:
            if request.workspace_spec is not None:
                materialized = RepoMaterializer().materialize(
                    workspace=Path(request.workspace.root), source=request.workspace_spec,
                )
                if materialized.repo_path.resolve() != Path(request.workspace.root).resolve():
                    return self._failure("Materialized repository is outside the granted root", blocked=True)
            boundary = WorkspaceBoundary(request.workspace)
            datasets = resolve_dataset_refs(self.resource_layout.dataset_root, request_dataset_refs(request))
        except DeadlineExceededError:
            raise
        except (OSError, ValueError, WorkspacePermissionError, RepoMaterializerError,
                DatasetResolutionError, ArtifactReadError) as error:
            return self._failure(str(error), blocked=True)
        workspace_id = request.workspace_id or (
            request.workspace_spec.workspace_id if request.workspace_spec else "workspace"
        )
        binding = EnvironmentBinding(
            EnvironmentManager(env_root=self.resource_layout.env_root),
            run_id=request.run_id, workspace_id=workspace_id,
            hard_constraint=request.environment_spec.python_version,
        )
        observer = WorkspaceObserver(boundary)
        output_root = request.output_dir or str(boundary.root / ".resagent2" / request.task_id)
        runner = ProcessRunner(boundary)
        tools = (
            ListFilesTool(boundary), ReadFileTool(boundary), SearchTextTool(boundary),
            ReadArtifactTool(RegisteredArtifactReader(request.input_artifacts, run_id=request.run_id)),
            PrepareEnvironmentTool(binding, allowed=request.permissions.prepare_environment),
            RunSetupTool(runner, binding, log_dir=f"{output_root}/setup",
                         timeout_seconds=request.budget.timeout_seconds,
                         allowed=request.permissions.execute_commands and request.permissions.prepare_environment),
            AuditEnvTool(binding, allowed=request.permissions.execute_commands),
            RunCommandTool(
                runner, binding,
                timeout_seconds=request.budget.timeout_seconds,
                extra_env=dataset_env_overrides(self.resource_layout.dataset_root, datasets),
                log_dir=f"{output_root}/commands",
                allowed=request.permissions.execute_commands,
            ),
            AskUserTool(), FinishTool(),
        )
        definition = AgentDefinition(
            name="experiment", owner=AgentOwner.EXPERIMENT,
            system_prompt=EXPERIMENT_PROMPT, tools=tools, llm_client=self.llm_client,
            context_builder=lambda request, state, limit: build_context(
                request, state, binding=binding, datasets=datasets, max_context_tokens=limit,
            ),
            permission_policy=OperationPermissionPolicy(tools, boundary=boundary, binding=binding, request=request),
            completion_check=ExperimentCompletionCheck(observer, output_dir=request.output_dir),
            action_type=ExperimentAction, max_context_tokens=self.max_context_tokens,
        )
        initial_memory = {"command_count": 0, "experiment_success_count": 0}
        if request.parent_session_id is None:
            initial_memory["workspace_snapshot"] = observer.snapshot().to_memory()
        return self.loop.run(
            definition, request,
            session_id=task_session_id(request.run_id, request.task_id, request.attempt_number),
            initial_memory=initial_memory,
        )
