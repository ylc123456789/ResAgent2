"""Reusable operations and presentation; no Tool entry points or Agent loop."""

from .artifacts import (
    ArtifactReadError,
    RegisteredArtifactReader,
    build_module_report,
    media_type_for,
)
from .context import workspace_context
from .dataset import (
    DatasetAvailability,
    DatasetCatalog,
    DatasetResolutionError,
    dataset_context,
    dataset_env_overrides,
    resolve_dataset_refs,
)
from .environment import (
    mirror_env_overrides,
    EnvironmentBinding,
    EnvironmentManager,
    EnvironmentManagerError,
    PreparedEnvironment,
    find_conda,
    HardwareAudit,
)
from .git import (
    GitBaseline,
    GitWorkspace,
    GitWorkspaceError,
)
from .process import (
    CommandPermissionDecision,
    ProcessRunner,
    UnsafeCommandError,
    VerificationCommandPolicy,
    parse_command,
)
from .repo import (
    MaterializedRepo,
    RepoMaterializer,
    RepoMaterializerError,
)
from .resources import ResourceLayout
from .snapshot import (
    WorkspaceObserver,
    WorkspaceSnapshot,
    snapshot_workspace,
)
from .workspace import (
    WorkspaceBoundary,
    WorkspacePermissionError,
)

__all__ = [
    'ArtifactReadError',
    'CommandPermissionDecision',
    'DatasetAvailability',
    'DatasetCatalog',
    'DatasetResolutionError',
    'EnvironmentBinding',
    'EnvironmentManager',
    'EnvironmentManagerError',
    'GitBaseline',
    'GitWorkspace',
    'GitWorkspaceError',
    'HardwareAudit',
    'MaterializedRepo',
    'PreparedEnvironment',
    'ProcessRunner',
    'RegisteredArtifactReader',
    'RepoMaterializer',
    'RepoMaterializerError',
    'ResourceLayout',
    'UnsafeCommandError',
    'VerificationCommandPolicy',
    'WorkspaceBoundary',
    'WorkspaceObserver',
    'WorkspacePermissionError',
    'WorkspaceSnapshot',
    'build_module_report',
    'dataset_context',
    'dataset_env_overrides',
    'find_conda',
    'media_type_for',
    'mirror_env_overrides',
    'parse_command',
    'resolve_dataset_refs',
    'snapshot_workspace',
    'workspace_context',
]
