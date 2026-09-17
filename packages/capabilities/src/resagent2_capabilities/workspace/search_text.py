"""Search text tool."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import NonEmptyStr, RuntimeModel
from resagent2_components.workspace import WorkspaceBoundary

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
