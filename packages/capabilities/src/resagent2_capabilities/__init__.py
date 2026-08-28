"""Concrete, assembable capabilities for ResAgent2 Agents.

The runtime package only defines how an Agent runs (the Agentic Loop and the
Tool protocol). This package provides what an Agent can do: filesystem, Git,
process, artifact, repository, environment, dataset and hardware primitives.
Agents assemble the subset they need through their Tool Profile.
"""

from .artifacts import ArtifactReadError, RegisteredArtifactReader
from .dataset import DatasetCache, mirror_env_overrides
from .environment import (
    EnvironmentManager,
    EnvironmentManagerError,
    env_id,
    env_spec,
    find_conda,
    project_slug,
    resource_root,
)
from .git import GitWorkspace, GitWorkspaceError
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
    CommandPermissionPolicy,
    ProcessRunner,
    UnsafeCommandError,
    parse_command,
)
from .repo import MaterializedRepo, RepoMaterializer, RepoMaterializerError
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
    "CommandPermissionDecision",
    "CommandPermissionPolicy",
    "CreateFileInput",
    "CreateFileTool",
    "DatasetCache",
    "EnvironmentManager",
    "EnvironmentManagerError",
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
    "RunVerificationInput",
    "RunVerificationTool",
    "SearchTextInput",
    "SearchTextTool",
    "UnsafeCommandError",
    "WorkspaceBoundary",
    "WorkspacePermissionError",
    "env_id",
    "env_spec",
    "find_conda",
    "media_type_for",
    "mirror_env_overrides",
    "parse_command",
    "project_slug",
    "resource_root",
]
