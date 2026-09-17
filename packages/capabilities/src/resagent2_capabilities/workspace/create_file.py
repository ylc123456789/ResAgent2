"""Create file tool."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel

from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import NonEmptyStr, RuntimeModel
from resagent2_components.workspace import WorkspaceBoundary

class CreateFileInput(RuntimeModel):
    """Create one new UTF-8 workspace file."""

    path: NonEmptyStr
    content: str


class CreateFileTool:
    """Create a file only when its target does not exist."""

    name = "create_file"
    input_model = CreateFileInput

    def __init__(self, boundary: WorkspaceBoundary) -> None:
        self.boundary = boundary

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(CreateFileInput, arguments)
        path = self.boundary.resolve_write_file(args.path, must_be_new=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            handle.write(args.content)
        revision = int(state.memory.get("edit_revision", 0)) + 1
        return ToolObservation(
            summary=f"Created {args.path}",
            value={"path": args.path, "bytes": len(args.content.encode("utf-8"))},
            memory_updates={"edit_revision": revision},
        )
