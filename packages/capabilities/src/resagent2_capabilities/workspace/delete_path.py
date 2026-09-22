"""Delete an exact workspace entry after inspecting its complete target set."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, cast

from pydantic import BaseModel

from resagent2_components.workspace import (
    DeleteInterruptedError,
    WorkspaceBoundary,
    WorkspacePermissionError,
)
from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import NonEmptyStr, RuntimeModel


class DeletePathInput(RuntimeModel):
    """Delete one exact path; nonempty directories require confirmation."""

    path: NonEmptyStr
    recursive: bool = False


class DeletePathTool:
    """Structured deletion with a prepared target snapshot for approval."""

    name = "delete_path"
    input_model = DeletePathInput

    def __init__(self, boundary: WorkspaceBoundary) -> None:
        self.boundary = boundary

    def prepare(self, arguments: BaseModel) -> dict[str, Any]:
        args = cast(DeletePathInput, arguments)
        return self.boundary.prepare_delete(args.path, recursive=args.recursive)

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        snapshot = self.prepare(arguments)
        if snapshot["requires_confirmation"]:
            raise WorkspacePermissionError("recursive deletion requires confirmation of its snapshot")
        return self.execute_prepared(state, arguments, snapshot)

    def execute_prepared(
        self, state: AgentState, arguments: BaseModel, snapshot: dict[str, Any],
    ) -> ToolObservation:
        """Execute the snapshot supplied by the trusted operation approval path."""
        args = cast(DeletePathInput, arguments)
        path = PurePosixPath(args.path.replace("\\", "/")).as_posix()
        if path != snapshot["path"] or args.recursive != snapshot["recursive"]:
            raise WorkspacePermissionError("delete snapshot does not match the requested target")
        try:
            deleted = self.boundary.delete_prepared(snapshot)
            remaining: list[str] = []
            error = None
        except DeleteInterruptedError as failure:
            deleted, remaining, error = failure.deleted, failure.remaining, str(failure)
        revision = int(state.memory.get("edit_revision", 0)) + 1
        return ToolObservation(
            ok=error is None,
            summary=(f"Deleted {snapshot['path']}" if error is None
                     else f"Deletion interrupted after {len(deleted)} entries: {error}"),
            value={"path": snapshot["path"], "deleted_paths": deleted,
                   "remaining_paths": remaining, "error": error},
            memory_updates={"edit_revision": revision},
        )
