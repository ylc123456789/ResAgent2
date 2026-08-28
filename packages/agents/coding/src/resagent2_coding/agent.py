"""Native Coding Agent ModulePort built on the shared AgentLoop."""

from __future__ import annotations

from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    ArtifactCandidate,
    Capability,
    CodeModifyResult,
    CodeUnderstandResult,
    ErrorCode,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    ModuleTaskRequest,
    WorkspaceMode,
)
from resagent2_capabilities import (
    CreateFileTool,
    GitDiffTool,
    GitWorkspace,
    GitWorkspaceError,
    ListFilesTool,
    ProcessRunner,
    ReadArtifactTool,
    ReadFileTool,
    RegisteredArtifactReader,
    ReplaceTextTool,
    RepoMaterializer,
    RepoMaterializerError,
    RunVerificationTool,
    SearchTextTool,
    WorkspaceBoundary,
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

from .completion import CodeModifyCompletionCheck, CodeUnderstandCompletionCheck
from .context import MODIFY_PROMPT, UNDERSTAND_PROMPT, build_context
from .models import CodeModifyAction, CodeUnderstandAction


class NativeCodingAgent:
    """Implement code_understand and code_modify without legacy code."""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        store: SessionStore | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.loop = AgentLoop(store=store or InMemorySessionStore())

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

    @staticmethod
    def _resolve_output_root(request: ModuleTaskRequest, boundary: WorkspaceBoundary) -> Path:
        """Return the absolute audit-output directory for this attempt.

        Prefer the scheduler-provided ``output_dir`` (Run data directory); fall
        back to the legacy workspace-relative ``.resagent2/runs/...`` location.
        """
        root = Path(
            request.output_dir
            or f".resagent2/runs/{request.run_id}/{request.task_id}/"
            f"attempt_{request.attempt_number}"
        )
        return root if root.is_absolute() else boundary.root / root

    def invoke(self, request: ModuleTaskRequest) -> ModuleResult:
        if request.capability not in {
            Capability.CODE_UNDERSTAND,
            Capability.CODE_MODIFY,
        }:
            return self._failure("NativeCodingAgent received a non-Coding capability")
        if request.workspace is None:
            return self._failure("NativeCodingAgent requires a WorkspaceGrant", blocked=True)
        if (
            request.capability == Capability.CODE_MODIFY
            and request.workspace.mode != WorkspaceMode.READ_WRITE
        ):
            return self._failure("code_modify requires a read_write workspace", blocked=True)

        # Deterministically prepare/reuse the repository before the loop.
        if request.workspace_spec is not None:
            try:
                materialized = RepoMaterializer().materialize(
                    workspace=Path(request.workspace.root),
                    source=request.workspace_spec,
                )
            except RepoMaterializerError as error:
                return self._failure(str(error), blocked=True)
            if materialized.repo_path.resolve() != Path(request.workspace.root).resolve():
                return self._failure(
                    "materialized repository is not the granted workspace root",
                    blocked=True,
                )

        try:
            boundary = WorkspaceBoundary(request.workspace)
            repository = GitWorkspace(boundary)
            repository.require_clean()
        except (OSError, GitWorkspaceError, WorkspacePermissionError) as error:
            return self._failure(str(error), blocked=True)

        output_root = self._resolve_output_root(request, boundary)

        common_tools = (
            ListFilesTool(boundary),
            ReadFileTool(boundary),
            SearchTextTool(boundary),
            ReadArtifactTool(RegisteredArtifactReader(request.input_artifacts)),
            GitDiffTool(repository),
            AskUserTool(),
            FinishTool(),
        )
        if request.capability == Capability.CODE_UNDERSTAND:
            definition = AgentDefinition(
                name="coding-understand",
                owner=AgentOwner.CODING,
                system_prompt=UNDERSTAND_PROMPT,
                tools=common_tools,
                llm_client=self.llm_client,
                context_builder=build_context,
                permission_policy=AllowListPermissionPolicy(
                    {tool.name for tool in common_tools}
                ),
                completion_check=CodeUnderstandCompletionCheck(repository),
                action_type=CodeUnderstandAction,
                result_type=CodeUnderstandResult,
            )
        else:
            write_tools = (
                CreateFileTool(boundary),
                ReplaceTextTool(boundary),
                RunVerificationTool(
                    ProcessRunner(boundary),
                    repository,
                    log_root=f"{output_root}/verification",
                    timeout_seconds=request.budget.timeout_seconds,
                ),
            )
            tools = (*common_tools, *write_tools)
            definition = AgentDefinition(
                name="coding-modify",
                owner=AgentOwner.CODING,
                system_prompt=MODIFY_PROMPT,
                tools=tools,
                llm_client=self.llm_client,
                context_builder=build_context,
                permission_policy=AllowListPermissionPolicy({tool.name for tool in tools}),
                completion_check=CodeModifyCompletionCheck(
                    repository,
                    boundary,
                    output_root=str(output_root),
                ),
                action_type=CodeModifyAction,
                result_type=CodeModifyResult,
            )

        result = self.loop.run(
            definition,
            request,
            session_id=f"session_{request.task_id}_{request.attempt_number}",
            initial_memory={"edit_revision": 0},
        )
        if result.status == ModuleStatus.FAILED and repository.changed_paths():
            patch_path = repository.write_patch(output_root / "failed_changes.patch")
            error = result.error
            if error is not None:
                error = error.model_copy(update={"retryable": False})
            return result.model_copy(
                update={
                    "artifacts": [
                        ArtifactCandidate(
                            kind="code_patch",
                            path="failed_changes.patch",
                            media_type="text/x-diff",
                            summary="Diagnostic patch from failed Coding Attempt",
                            metadata={"diagnostic": True},
                            content=repository.diff(),
                        )
                    ],
                    "error": error,
                }
            )
        return result
