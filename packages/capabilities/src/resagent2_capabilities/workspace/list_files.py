"""List files tool."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import RuntimeModel
from resagent2_components.workspace import WorkspaceBoundary

class ListFilesInput(RuntimeModel):
    """Bounded workspace listing request."""

    path: str = "."
    max_files: int = Field(default=200, ge=1, le=2000)


class ListFilesTool:
    """List readable files without following escaping symlinks."""

    name = "list_files"
    input_model = ListFilesInput

    def __init__(self, boundary: WorkspaceBoundary) -> None:
        self.boundary = boundary

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(ListFilesInput, arguments)
        files = self.boundary.iter_files(args.path)
        truncated = len(files) > args.max_files
        return ToolObservation(
            summary=f"Listed {min(len(files), args.max_files)} workspace files",
            value={
                "path": args.path,
                "paths": files[: args.max_files],
                "truncated": truncated,
            },
        )
