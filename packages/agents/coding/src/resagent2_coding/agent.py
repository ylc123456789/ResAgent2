"""Native Coding Agent using one request, action and completion protocol."""

from __future__ import annotations

from pathlib import Path

from resagent2_contracts import (
    AgentOwner, AgentRequest, AgentResult, ArtifactCandidate, ErrorCode,
    ModuleError, ModuleStatus, task_session_id,
)
from resagent2_capabilities import (
    CreateFileTool, DeletePathTool, GitDiffTool, ListFilesTool,
    ReadArtifactTool, ReadFileTool, ReplaceTextTool, RunShellTool, SearchTextTool,
)
from resagent2_components import (
    ArtifactReadError, DatasetResolutionError, GitWorkspace, GitWorkspaceError, ProcessRunner,
    RegisteredArtifactReader, RepoMaterializer, RepoMaterializerError,
    ResourceLayout, WorkspaceBoundary, WorkspacePermissionError,
    GitBaseline, dataset_env_overrides, request_dataset_refs, resolve_dataset_refs, preserve_execution_record,
)
from resagent2_runtime import (
    DEFAULT_AGENT_CONTEXT_TOKENS, AgentDefinition, AgentLoop, OperationReviewer,
    AskUserTool, FinishTool,
    InMemorySessionStore, LLMClient, SessionStore,
)

from .completion import CodingCompletionCheck
from .context import CODING_PROMPT, build_context
from resagent2_capabilities.permissions import OperationPermissionPolicy
from resagent2_runtime.budget import DeadlineExceededError, execution_budget


class NativeCodingAgent:
    """Inspect and edit authorized code through a single Agent protocol."""

    description = (
        "Understand, implement and repair code, including experiment entry points, and "
        "verify correctness under the granted permissions. Bounded correctness checks "
        "belong here; research measurements belong to Experiment. Code inspection can "
        "finish with a report; changes and command execution are optional."
    )

    def __init__(
        self, llm_client: LLMClient, *, store: SessionStore | None = None,
        resource_layout: ResourceLayout | None = None,
        max_context_tokens: int = DEFAULT_AGENT_CONTEXT_TOKENS,
        operation_reviewer: OperationReviewer | None = None,
    ) -> None:
        if max_context_tokens < 1:
            raise ValueError("max_context_tokens must be positive")
        self.llm_client = llm_client
        self.loop = AgentLoop(store=store or InMemorySessionStore())
        self.resource_layout = resource_layout or ResourceLayout.from_env()
        self.max_context_tokens = max_context_tokens
        self.operation_reviewer = operation_reviewer

    @staticmethod
    def _failure(message: str, *, blocked: bool = False) -> AgentResult:
        return AgentResult(
            status=ModuleStatus.BLOCKED if blocked else ModuleStatus.FAILED,
            report=message,
            error=ModuleError(code=ErrorCode.INVALID_INPUT, message=message, retryable=False),
        )

    def terminate_session(self, session_id: str, reason: str) -> None:
        self.loop.terminate_session(session_id, reason)

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

        initial_memory: dict = {}
        if request.parent_session_id is not None:
            try:
                prior = self.loop.store.load(request.parent_session_id)
                baseline = GitBaseline.from_memory(prior.memory.get("workspace_snapshot"))
            except (OSError, ValueError, KeyError):
                return self._failure("Resumed Coding attempt has no persisted Git baseline", blocked=True)
        else:
            baseline = repository.snapshot()
            initial_memory["workspace_snapshot"] = baseline.to_memory()

        output_root = request.output_dir or str(boundary.root / ".resagent2" / request.task_id)
        runner = ProcessRunner(boundary)
        tools = (
            ListFilesTool(boundary), ReadFileTool(boundary), SearchTextTool(boundary),
            ReadArtifactTool(RegisteredArtifactReader(request.input_artifacts, run_id=request.run_id)),
            GitDiffTool(repository, baseline=baseline),
            CreateFileTool(boundary), ReplaceTextTool(boundary), DeletePathTool(boundary),
            RunShellTool(
                runner, repository=repository,
                timeout_seconds=request.budget.timeout_seconds,
                extra_env=dataset_env_overrides(self.resource_layout.dataset_root, datasets),
                log_dir=f"{output_root}/commands",
                allowed=request.permissions.execute_commands,
            ),
            AskUserTool(), FinishTool(),
        )
        definition = AgentDefinition(
            name="coding", owner=AgentOwner.CODING, system_prompt=CODING_PROMPT,
            tools=tools, llm_client=self.llm_client,
            context_builder=lambda request, state, limit: build_context(
                request, state, datasets=datasets,
            ),
            permission_policy=OperationPermissionPolicy(tools, boundary=boundary, request=request),
            operation_reviewer=self.operation_reviewer,
            completion_check=CodingCompletionCheck(
                repository, boundary, baseline=baseline, output_dir=request.output_dir,
            ),
            max_context_tokens=self.max_context_tokens,
        )
        result = self.loop.run(
            definition, request,
            session_id=task_session_id(request.run_id, request.task_id, request.attempt_number),
            initial_memory=initial_memory,
        )
        result = preserve_execution_record(result, self.loop.store)
        if (result.status not in {ModuleStatus.FAILED, ModuleStatus.BLOCKED}
                or any(item.kind == "code_patch" for item in result.artifacts)):
            return result
        try:
            patch = repository.diff_since(baseline)
        except (OSError, GitWorkspaceError) as error:
            # Diagnostics must not replace a persisted failure or restart work
            # whose workspace changes could not be inspected.
            return result.model_copy(update={"error": result.error.model_copy(update={
                "retryable": False,
                "details": {**result.error.details, "diagnostic_patch_error": str(error)},
            })})
        if patch:
            error = result.error.model_copy(update={"retryable": False}) if result.error else None
            return result.model_copy(update={
                "artifacts": [*result.artifacts, ArtifactCandidate(
                    kind="code_patch", path="failed_changes.patch", media_type="text/x-diff",
                    summary="Diagnostic patch from failed Coding attempt",
                    metadata={"diagnostic": True}, content=patch,
                )],
                "error": error,
            })
        return result
