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
    AuditEnvTool,
    CreateFileTool,
    EnvironmentBinding,
    EnvironmentManager,
    GitDiffTool,
    GitWorkspace,
    GitWorkspaceError,
    ListFilesTool,
    PrepareEnvironmentTool,
    ProcessRunner,
    ReadArtifactTool,
    ReadFileTool,
    RegisteredArtifactReader,
    ReplaceTextTool,
    RepoMaterializer,
    RepoMaterializerError,
    ResourceLayout,
    RunSetupTool,
    RunVerificationTool,
    SearchTextTool,
    WorkspaceBoundary,
    WorkspacePermissionError,
    WorkspaceSnapshot,
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

from .completion import (
    CodeModifyCompletionCheck,
    CodeUnderstandCompletionCheck,
    derive_control_state,
)
from .context import MODIFY_PROMPT, UNDERSTAND_PROMPT, build_context
from .models import CodeModifyAction, CodeUnderstandAction


class NativeCodingAgent:
    """Implement code_understand and code_modify without legacy code."""

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
        except (OSError, GitWorkspaceError, WorkspacePermissionError) as error:
            return self._failure(str(error), blocked=True)

        # Capture this Attempt's baseline: previous tasks' accepted changes are
        # the starting state, and only this Attempt's increment counts. On
        # resume the baseline is restored from persisted Session memory, never
        # re-snapshotted, so edits made before the pause are not mistaken for
        # the Attempt's starting state (ADR-0011 §2).
        baseline = repository.snapshot()
        initial_memory: dict = {"edit_revision": 0}
        if request.parent_session_id is not None:
            prior = None
            try:
                prior = self.loop.store.load(request.parent_session_id)
            except Exception:
                prior = None
            raw = prior.memory.get("workspace_snapshot") if prior is not None else None
            try:
                snapshot = WorkspaceSnapshot.from_memory(raw)
            except ValueError:
                snapshot = None
            if snapshot is None or snapshot.git_baseline is None:
                return self._failure(
                    "resumed Coding Attempt has no persisted Git baseline",
                    blocked=True,
                )
            baseline = snapshot.git_baseline
        else:
            initial_memory["workspace_snapshot"] = WorkspaceSnapshot(
                tree_hash=baseline.tree_hash
            ).to_memory()

        common_tools = (
            ListFilesTool(boundary),
            ReadFileTool(boundary),
            SearchTextTool(boundary),
            ReadArtifactTool(RegisteredArtifactReader(request.input_artifacts)),
            GitDiffTool(repository, baseline=baseline),
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
                completion_check=CodeUnderstandCompletionCheck(repository, baseline),
                action_type=CodeUnderstandAction,
                result_type=CodeUnderstandResult,
            )
        else:
            if request.output_dir is None:
                return self._failure("CodingAgent requires an output_dir", blocked=True)
            output_root = Path(request.output_dir)
            workspace_id = request.workspace_id or (
                request.workspace_spec.workspace_id if request.workspace_spec else None
            )
            if workspace_id is None:
                return self._failure("code_modify requires a workspace_id", blocked=True)
            manager = EnvironmentManager(env_root=self.resource_layout.env_root)
            binding = EnvironmentBinding(
                manager,
                run_id=request.run_id,
                workspace_id=workspace_id,
                hard_constraint=request.environment_spec.python_version,
            )
            runner = ProcessRunner(boundary)
            write_tools = (
                CreateFileTool(boundary),
                ReplaceTextTool(boundary),
                PrepareEnvironmentTool(binding),
                RunSetupTool(
                    runner,
                    binding,
                    log_dir=f"{output_root}/setup",
                    timeout_seconds=request.budget.timeout_seconds,
                ),
                AuditEnvTool(binding),
                RunVerificationTool(
                    runner,
                    repository,
                    log_root=f"{output_root}/verification",
                    timeout_seconds=request.budget.timeout_seconds,
                    baseline=baseline,
                    env_binding=binding,
                ),
            )
            tools = (*common_tools, *write_tools)

            def context_builder(request, state):
                return build_context(
                    request, state, control_state=derive_control_state(state, binding)
                )

            definition = AgentDefinition(
                name="coding-modify",
                owner=AgentOwner.CODING,
                system_prompt=MODIFY_PROMPT,
                tools=tools,
                llm_client=self.llm_client,
                context_builder=context_builder,
                permission_policy=AllowListPermissionPolicy({tool.name for tool in tools}),
                completion_check=CodeModifyCompletionCheck(
                    repository,
                    boundary,
                    output_root=str(output_root),
                    baseline=baseline,
                ),
                action_type=CodeModifyAction,
                result_type=CodeModifyResult,
            )

        result = self.loop.run(
            definition,
            request,
            session_id=f"session_{request.task_id}_{request.attempt_number}",
            initial_memory=initial_memory,
        )
        if (
            request.capability == Capability.CODE_MODIFY
            and result.status == ModuleStatus.FAILED
            and repository.changed_paths_since(baseline)
        ):
            patch_path = repository.write_patch_since(
                baseline, output_root / "failed_changes.patch"
            )
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
                            content=repository.diff_since(baseline),
                        )
                    ],
                    "error": error,
                }
            )
        return result
