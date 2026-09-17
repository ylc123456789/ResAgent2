"""Read file tool."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import NonEmptyStr, RuntimeModel

from resagent2_components.context import remember_source as _remember
from resagent2_components.text import MAX_READ_CHARS, slice_text_lines
from resagent2_components.workspace import WorkspaceBoundary

class ReadFileInput(RuntimeModel):
    """Read one optional line range from a workspace file."""

    path: NonEmptyStr
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class ReadFileTool:
    """Read one optional line range through a WorkspaceBoundary."""

    name = "read_file"
    input_model = ReadFileInput
    model_guidance = (
        "If a read result is truncated, search for the symbol then read a "
        "bounded start_line/end_line range; do not repeat the same unbounded read."
    )

    def __init__(
        self,
        boundary: WorkspaceBoundary,
        *,
        max_chars: int = MAX_READ_CHARS,
        max_bytes: int = 1_000_000,
    ) -> None:
        self.boundary = boundary
        self.max_chars = max_chars
        self.max_bytes = max_bytes

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(ReadFileInput, arguments)
        if args.start_line and args.end_line and args.end_line < args.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        path = self.boundary.resolve_read_file(args.path)
        if path.stat().st_size > self.max_bytes:
            raise ValueError(f"file is too large to read: {path.stat().st_size} bytes")
        text = path.read_text(encoding="utf-8", errors="replace")
        return ToolObservation(
            summary=f"Read {args.path}",
            value={
                "path": args.path,
                **slice_text_lines(
                    text, start_line=args.start_line, end_line=args.end_line,
                    max_chars=self.max_chars,
                ),
            },
            memory_updates={"read_paths": _remember(state, "read_paths", args.path)},
        )
