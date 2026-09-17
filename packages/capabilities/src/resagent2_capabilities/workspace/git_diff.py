"""Git diff tool."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import RuntimeModel
from resagent2_components.git import GitBaseline, GitWorkspace

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
