"""Concrete, assembable capabilities for ResAgent2 Agents.

The runtime package only defines how an Agent runs (the Agentic Loop and the
Tool protocol). This package provides what an Agent can do: filesystem, Git,
process, artifact, repository, environment, dataset and hardware primitives.
Agents assemble the subset they need through their Tool Profile.
"""

from .artifacts import ArtifactReadError, RegisteredArtifactReader
from .dataset import (
    DatasetResolutionError,
    dataset_env_overrides,
    mirror_env_overrides,
    resolve_dataset_refs,
)
from .environment import (
    EnvironmentBinding,
    EnvironmentManager,
    EnvironmentManagerError,
    PreparedEnvironment,
    find_conda,
)
from .environment_tools import (
    AuditEnvInput,
    AuditEnvTool,
    PrepareEnvironmentInput,
    PrepareEnvironmentTool,
    RunSetupInput,
    RunSetupTool,
    SetupCommandPolicy,
)
from .git import GitBaseline, GitWorkspace, GitWorkspaceError
from .hardware import HardwareAudit
from .literature import (
    ArtifactRegistrationPort,
    ArxivLiteratureBackend,
    LiteraturePaper,
    LiteratureSearchBackend,
    LiteratureSearchError,
    LiteratureSearchTool,
    LiteratureSearchToolInput,
)
from .process import (
    CommandPermissionDecision,
    ProcessRunner,
    UnsafeCommandError,
    VerificationCommandPolicy,
    parse_command,
)
from .repo import MaterializedRepo, RepoMaterializer, RepoMaterializerError
from .resources import ResourceLayout
from .workspace import WorkspaceBoundary, WorkspacePermissionError
from .workspace_tools import (
    CreateFileInput,
    CreateFileTool,
    GitDiffInput,
    GitDiffTool,
    ListFilesInput,
    ListFilesTool,
    ReadArtifactInput,
    ReadArtifactTool,
    ReadFileInput,
    ReadFileTool,
    ReplaceTextInput,
    ReplaceTextTool,
    RunVerificationInput,
    RunVerificationTool,
    SearchTextInput,
    SearchTextTool,
    media_type_for,
)

__all__ = [
    "ArtifactReadError",
    "ArtifactRegistrationPort",
    "ArxivLiteratureBackend",
    "AuditEnvInput",
    "AuditEnvTool",
    "CommandPermissionDecision",
    "CreateFileInput",
    "CreateFileTool",
    "DatasetResolutionError",
    "EnvironmentBinding",
    "EnvironmentManager",
    "EnvironmentManagerError",
    "GitBaseline",
    "GitDiffInput",
    "GitDiffTool",
    "GitWorkspace",
    "GitWorkspaceError",
    "HardwareAudit",
    "ListFilesInput",
    "ListFilesTool",
    "LiteraturePaper",
    "LiteratureSearchBackend",
    "LiteratureSearchError",
    "LiteratureSearchTool",
    "LiteratureSearchToolInput",
    "MaterializedRepo",
    "PreparedEnvironment",
    "PrepareEnvironmentInput",
    "PrepareEnvironmentTool",
    "ProcessRunner",
    "ReadArtifactInput",
    "ReadArtifactTool",
    "ReadFileInput",
    "ReadFileTool",
    "RegisteredArtifactReader",
    "ReplaceTextInput",
    "ReplaceTextTool",
    "RepoMaterializer",
    "RepoMaterializerError",
    "ResourceLayout",
    "RunSetupInput",
    "RunSetupTool",
    "RunVerificationInput",
    "RunVerificationTool",
    "SearchTextInput",
    "SearchTextTool",
    "SetupCommandPolicy",
    "UnsafeCommandError",
    "WorkspaceBoundary",
    "WorkspacePermissionError",
    "dataset_env_overrides",
    "find_conda",
    "media_type_for",
    "mirror_env_overrides",
    "parse_command",
    "resolve_dataset_refs",
]
