"""Native Coding Agent using one request, action and completion protocol."""

from __future__ import annotations

from pathlib import Path

from resagent2_contracts import (
    AgentOwner, AgentRequest, AgentResult, ArtifactCandidate, ErrorCode,
    ModuleError, ModuleStatus, task_session_id,
)
from resagent2_capabilities import (
    AuditEnvTool, CreateFileTool, DeletePathTool, GitDiffTool, ListFilesTool,
    PrepareEnvironmentTool, ReadArtifactTool, ReadFileTool, ReplaceTextTool,
    RunSetupTool, SearchTextTool,
)
from resagent2_components import (
    ArtifactReadError, DatasetResolutionError, EnvironmentBinding,
    EnvironmentManager, GitWorkspace, GitWorkspaceError, ProcessRunner,
    RegisteredArtifactReader, RepoMaterializer, RepoMaterializerError,
    ResourceLayout, WorkspaceBoundary, WorkspacePermissionError,
    WorkspaceSnapshot, dataset_env_overrides, request_dataset_refs, resolve_dataset_refs,
)
from resagent2_runtime import (
    DEFAULT_AGENT_CONTEXT_TOKENS, AgentDefinition, AgentLoop,
    AskUserTool, FinishTool,
    InMemorySessionStore, LLMClient, SessionStore,
)

from .completion import CodingCompletionCheck, derive_control_state
from .context import CODING_PROMPT, build_context
from .models import CodingAction
from .verification import RunVerificationTool
from resagent2_components.permissions import OperationPermissionPolicy
from resagent2_runtime.budget import DeadlineExceededError, execution_budget


class NativeCodingAgent:
    """Inspect and edit authorized code through a single Agent protocol."""

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
        if request.agent != AgentOwner.CODING:
            return self._failure("NativeCodingAgent received a non-Coding request")
        if request.workspace is None:
            return self._failure("Coding requires a WorkspaceGrant", blocked=True)
        try:
            if request.workspace_spec is not None:
                materialized = RepoMaterializer().materialize(
                    workspace=Path(request.workspace.root), source=request.workspace_spec,
                )
                if materialized.repo_path.resolve() != Path(request.workspace.root).resolve():
                    return self._failure("Materialized repository is outside the granted root", blocked=True)
            boundary = WorkspaceBoundary(request.workspace)
            repository = GitWorkspace(boundary)
            datasets = resolve_dataset_refs(self.resource_layout.dataset_root, request_dataset_refs(request))
        except DeadlineExceededError:
            raise
        except (OSError, ValueError, GitWorkspaceError, WorkspacePermissionError,
                RepoMaterializerError, DatasetResolutionError, ArtifactReadError) as error:
            return self._failure(str(error), blocked=True)

        baseline = repository.snapshot()
        initial_memory: dict = {"edit_revision": 0}
        if request.parent_session_id is not None:
            try:
                prior = self.loop.store.load(request.parent_session_id)
                snapshot = WorkspaceSnapshot.from_memory(prior.memory.get("workspace_snapshot"))
            except (OSError, ValueError, KeyError):
                snapshot = None
            if snapshot is None or snapshot.git_baseline is None:
                return self._failure("Resumed Coding attempt has no persisted Git baseline", blocked=True)
            baseline = snapshot.git_baseline
        else:
            initial_memory["workspace_snapshot"] = WorkspaceSnapshot(tree_hash=baseline.tree_hash).to_memory()

        workspace_id = request.workspace_id or (
            request.workspace_spec.workspace_id if request.workspace_spec else "workspace"
        )
        binding = EnvironmentBinding(
            EnvironmentManager(env_root=self.resource_layout.env_root),
            run_id=request.run_id, workspace_id=workspace_id,
            hard_constraint=request.environment_spec.python_version,
        )
        output_root = request.output_dir or str(boundary.root / ".resagent2" / request.task_id)
        runner = ProcessRunner(boundary)
        tools = (
            ListFilesTool(boundary), ReadFileTool(boundary), SearchTextTool(boundary),
            ReadArtifactTool(RegisteredArtifactReader(request.input_artifacts, run_id=request.run_id)),
            GitDiffTool(repository, baseline=baseline),
            CreateFileTool(boundary), ReplaceTextTool(boundary), DeletePathTool(boundary),
            PrepareEnvironmentTool(binding, allowed=request.permissions.prepare_environment),
            RunSetupTool(runner, binding, log_dir=f"{output_root}/setup",
                         timeout_seconds=request.budget.timeout_seconds,
                         allowed=request.permissions.execute_commands and request.permissions.prepare_environment),
            AuditEnvTool(binding, allowed=request.permissions.execute_commands),
            RunVerificationTool(
                runner, repository, log_root=f"{output_root}/verification",
                timeout_seconds=request.budget.timeout_seconds,
                baseline=baseline, env_binding=binding,
                extra_env=dataset_env_overrides(self.resource_layout.dataset_root, datasets),
                allowed=request.permissions.execute_commands,
            ),
            AskUserTool(), FinishTool(),
        )
        definition = AgentDefinition(
            name="coding", owner=AgentOwner.CODING, system_prompt=CODING_PROMPT,
            tools=tools, llm_client=self.llm_client,
            context_builder=lambda request, state, limit: build_context(
                request, state, binding=binding, datasets=datasets,
                control_state=derive_control_state(state, binding), max_context_tokens=limit,
            ),
            permission_policy=OperationPermissionPolicy(tools, boundary=boundary, binding=binding, request=request),
            completion_check=CodingCompletionCheck(
                repository, boundary, baseline=baseline, env_binding=binding,
            ),
            action_type=CodingAction, max_context_tokens=self.max_context_tokens,
        )
        result = self.loop.run(
            definition, request,
            session_id=task_session_id(request.run_id, request.task_id, request.attempt_number),
            initial_memory=initial_memory,
        )
        if result.status in {ModuleStatus.FAILED, ModuleStatus.BLOCKED} and repository.changed_paths_since(baseline):
            error = result.error.model_copy(update={"retryable": False}) if result.error else None
            return result.model_copy(update={
                "artifacts": [*result.artifacts, ArtifactCandidate(
                    kind="code_patch", path="failed_changes.patch", media_type="text/x-diff",
                    summary="Diagnostic patch from failed Coding attempt",
                    metadata={"diagnostic": True}, content=repository.diff_since(baseline),
                )],
                "error": error,
            })
        return result
