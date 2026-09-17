"""Workspace file and Git-diff tools."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import NonEmptyStr, RuntimeModel

import os
import tempfile
from pathlib import Path

from pydantic import field_validator

from resagent2_components.context import remember_source as _remember
from resagent2_components.git import GitBaseline, GitWorkspace
from resagent2_components.text import MAX_READ_CHARS, slice_text_lines
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


class SearchTextInput(RuntimeModel):
    """Case-insensitive bounded literal substring search, not a regex search."""

    query: NonEmptyStr = Field(
        description="Case-insensitive literal substring; no regular expressions."
    )
    path: str = "."
    max_results: int = Field(default=20, ge=1, le=50)


class SearchTextTool:
    """Search readable text files without invoking a process."""

    name = "search_text"
    input_model = SearchTextInput
    model_guidance = (
        "Search is a case-insensitive literal substring match, not regex. "
        "a|b matches the literal text a|b; search alternatives separately. "
        "path may identify a workspace file or directory. max_results must be "
        "between 1 and 50. Use returned line numbers for a bounded read_file range."
    )

    def __init__(self, boundary: WorkspaceBoundary) -> None:
        self.boundary = boundary

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(SearchTextInput, arguments)
        matches: list[dict] = []
        observed: list[str] = []
        try:
            files = self.boundary.iter_files(args.path)
        except NotADirectoryError:
            # Directory resolution already enforced its grant; resolving the
            # file rechecks containment and symlinks. Never catch permission errors.
            path = self.boundary.resolve_read_file(args.path)
            files = [self.boundary.relative(path)]
        for relative in files:
            path = self.boundary.resolve_read_file(relative)
            if path.stat().st_size > 1_000_000:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            file_matched = False
            for number, line in enumerate(lines, start=1):
                if args.query.lower() not in line.lower():
                    continue
                matches.append({"path": relative, "line": number, "text": line[:200]})
                file_matched = True
                if len(matches) >= args.max_results:
                    break
            if file_matched:
                observed.append(relative)
            if len(matches) >= args.max_results:
                break
        read_paths = list(state.memory.get("read_paths", []))
        for relative in observed:
            if relative not in read_paths:
                read_paths.append(relative)
        return ToolObservation(
            summary=f"Found {len(matches)} matches for {args.query!r}",
            value={"matches": matches, "truncated": len(matches) >= args.max_results},
            memory_updates={"read_paths": read_paths},
        )


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


class GitDiffInput(RuntimeModel):
    """Bound the Git diff returned to the Agent context."""

    max_chars: int = Field(default=8_000, ge=1, le=20_000)


class GitDiffTool:
    """Expose the Attempt-relative Git patch (the increment since baseline).

    The Coding Agent always supplies its Attempt baseline, so the Agent sees the
    same increment the deterministic finalizer attributes (ADR-0011 §4).
    """

    name = "git_diff"
    input_model = GitDiffInput

    def __init__(
        self, repository: GitWorkspace, *, baseline: GitBaseline
    ) -> None:
        self.repository = repository
        self.baseline = baseline

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(GitDiffInput, arguments)
        diff = self.repository.diff_since(self.baseline)
        truncated = len(diff) > args.max_chars
        return ToolObservation(
            summary="Read current Git diff",
            value={"diff": diff[: args.max_chars], "truncated": truncated},
        )
