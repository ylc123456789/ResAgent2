"""Native Experiment Agent with one invocation and completion protocol."""

from __future__ import annotations

from pathlib import Path

from resagent2_contracts import (
    AgentOwner, AgentRequest, AgentResult, ErrorCode, ModuleError, ModuleStatus, WorkspaceMode, RecordedAnswer,
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
    dataset_env_overrides, request_dataset_refs, resolve_dataset_refs, read_artifact_json,
)
from resagent2_runtime import (
    DEFAULT_AGENT_CONTEXT_TOKENS, AgentDefinition, AgentLoop,
    AllowListPermissionPolicy, AskUserTool, FinishTool, InMemorySessionStore,
    LLMClient, SessionStore,
)

from .completion import ExperimentCompletionCheck
from .context import EXPERIMENT_PROMPT, build_context
from .models import ExperimentAction
from .tools import RunCommandTool


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
        confirmed_command = None
        if request.parent_session_id and request.confirm_before_experiment:
            try:
                prior = self.loop.store.load(request.parent_session_id)
                pending = prior.memory.get("pending_command_confirmation")
                reader = RegisteredArtifactReader(request.input_artifacts, run_id=request.run_id)
                for ref in request.input_artifacts:
                    if ref.kind != "answer" or ref.id not in request.resume_artifact_ids:
                        continue
                    answer = read_artifact_json(reader, ref.id, RecordedAnswer)
                    if (prior.run_id == request.run_id and prior.task_id == request.task_id
                            and prior.attempt_number == request.attempt_number
                            and prior.owner == AgentOwner.EXPERIMENT and pending
                            and answer.run_id == request.run_id and answer.task_id == request.task_id
                            and answer.attempt_number == request.attempt_number
                            and answer.question_text == "Pre-experiment confirmation is enabled. "
                                f"Confirm running the experiment command: {pending}"
                            and answer.values.get("approve", "").strip().lower()
                                in {"yes", "true", "approve", "approved", "确认", "同意", "是"}):
                        confirmed_command = pending
            except (OSError, ValueError, KeyError) as error:
                return self._failure(str(error))
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
                confirm_before_experiment=request.confirm_before_experiment,
                confirmed=request.experiment_confirmed,
                confirmed_command=confirmed_command,
                timeout_seconds=request.budget.timeout_seconds,
                extra_env=dataset_env_overrides(self.resource_layout.dataset_root, datasets),
                log_dir=f"{output_root}/commands",
                allowed=request.permissions.execute_commands,
            ),
            AskUserTool(), FinishTool(),
        )
        allowed = {tool.name for tool in tools}
        if not request.permissions.execute_commands:
            allowed -= {"run_command", "run_setup", "audit_env"}
        if not request.permissions.prepare_environment:
            allowed -= {"prepare_environment", "run_setup"}
        if request.workspace.mode != WorkspaceMode.READ_WRITE:
            allowed -= {"run_setup", "run_command"}
        definition = AgentDefinition(
            name="experiment", owner=AgentOwner.EXPERIMENT,
            system_prompt=EXPERIMENT_PROMPT, tools=tools, llm_client=self.llm_client,
            context_builder=lambda request, state, limit: build_context(
                request, state, binding=binding, datasets=datasets, max_context_tokens=limit,
            ),
            permission_policy=AllowListPermissionPolicy(allowed),
            completion_check=ExperimentCompletionCheck(observer),
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
