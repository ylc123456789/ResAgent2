"""Native Coding Agent ModulePort built on the shared AgentLoop."""

from __future__ import annotations

from resagent2_contracts import (
    AgentOwner,
    ArtifactCandidate,
    Capability,
    CodeModifyInput,
    CodeModifyResult,
    CodeUnderstandResult,
    ErrorCode,
    ModuleError,
    ModuleResult,
    ModuleStatus,
    ModuleTaskRequest,
    WorkspaceMode,
)
from resagent2_runtime import (
    AgentDefinition,
    AgentLoop,
    AllowListPermissionPolicy,
    AskUserTool,
    CreateFileTool,
    FinishTool,
    GitDiffTool,
    GitWorkspace,
    GitWorkspaceError,
    InMemorySessionStore,
    LLMClient,
    ListFilesTool,
    ProcessRunner,
    ReadArtifactTool,
    ReadFileTool,
    RegisteredArtifactReader,
    ReplaceTextTool,
    RunVerificationTool,
    SearchTextTool,
    SessionStore,
    WorkspaceBoundary,
    WorkspacePermissionError,
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

        inputs = request.inputs
        write_paths = inputs.allowed_paths if isinstance(inputs, CodeModifyInput) else []
        try:
            boundary = WorkspaceBoundary(request.workspace, write_paths=write_paths)
            repository = GitWorkspace(boundary)
        except (OSError, GitWorkspaceError, WorkspacePermissionError) as error:
            return self._failure(str(error), blocked=True)

        common_tools = (
            ListFilesTool(boundary),
            ReadFileTool(boundary),
            SearchTextTool(boundary),
            ReadArtifactTool(RegisteredArtifactReader(request.input_artifacts)),
            GitDiffTool(repository),
            AskUserTool(),
            FinishTool(),
        )
        output_root = (
            f".resagent2/runs/{request.run_id}/{request.task_id}/"
            f"attempt_{request.attempt_number}"
        )
        if request.capability == Capability.CODE_MODIFY:
            try:
                boundary.resolve_system_write(f"{output_root}/changes.patch")
            except WorkspacePermissionError as error:
                return self._failure(
                    "code_modify requires WorkspaceGrant access to its .resagent2 "
                    f"audit directory: {error}",
                    blocked=True,
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
            commands = list(inputs.verification_commands)
            write_tools = (
                CreateFileTool(boundary),
                ReplaceTextTool(boundary),
                RunVerificationTool(
                    ProcessRunner(boundary),
                    repository,
                    commands,
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
                    commands,
                    output_root=output_root,
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
            patch_path = repository.write_patch(f"{output_root}/failed_changes.patch")
            error = result.error
            if error is not None:
                error = error.model_copy(update={"retryable": False})
            return result.model_copy(
                update={
                    "artifacts": [
                        ArtifactCandidate(
                            kind="code_patch",
                            path=patch_path,
                            media_type="text/x-diff",
                            summary="Diagnostic patch from failed Coding Attempt",
                            metadata={"diagnostic": True},
                        )
                    ],
                    "error": error,
                }
            )
        return result
