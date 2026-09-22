"""Replace text tool."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import cast

from pydantic import BaseModel, field_validator

from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import NonEmptyStr, RuntimeModel
from resagent2_components.workspace import WorkspaceBoundary

class ReplaceTextInput(RuntimeModel):
    """Replace one exact text occurrence in an existing file."""

    path: NonEmptyStr
    old_text: str
    new_text: str

    @field_validator("old_text")
    @classmethod
    def _old_text_must_not_be_empty(cls, value: str) -> str:
        """Reject empty matches without stripping whitespace.

        ``old_text`` is an exact-text needle: leading/trailing spaces, tabs and
        newlines are significant (they encode Python indentation). Unlike
        ``NonEmptyStr``, it must never be strip-normalized.
        """
        if value == "":
            raise ValueError("old_text must not be empty")
        return value


class ReplaceTextTool:
    """Atomically apply an exactly-once text replacement."""

    name = "replace_text"
    input_model = ReplaceTextInput

    def __init__(self, boundary: WorkspaceBoundary, *, max_bytes: int = 1_000_000) -> None:
        self.boundary = boundary
        self.max_bytes = max_bytes

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(ReplaceTextInput, arguments)
        path = self.boundary.resolve_write_file(args.path)
        if path.stat().st_size > self.max_bytes:
            raise ValueError(f"file is too large to edit: {path.stat().st_size} bytes")
        text = path.read_text(encoding="utf-8")
        count = text.count(args.old_text)
        if count != 1:
            raise ValueError(f"old_text must match exactly once; found {count}")
        updated = text.replace(args.old_text, args.new_text, 1)
        if updated == text:
            raise ValueError("replacement does not change the file")
        mode = path.stat().st_mode
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                delete=False,
            ) as handle:
                handle.write(updated)
                temporary = Path(handle.name)
            os.chmod(temporary, mode)
            if self.boundary.resolve_write_file(args.path) != path:
                raise PermissionError("write target changed during replacement")
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        revision = int(state.memory.get("edit_revision", 0)) + 1
        return ToolObservation(
            summary=f"Replaced one exact match in {args.path}",
            value={"path": args.path},
            memory_updates={"edit_revision": revision},
        )
