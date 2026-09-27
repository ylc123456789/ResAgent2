"""Read registered artifacts through the model tool protocol."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import NonEmptyStr, RuntimeModel
from resagent2_components.artifacts import RegisteredArtifactReader
from resagent2_components.context import remember_source as _remember

class ReadArtifactInput(RuntimeModel):
    """Identify one registered ArtifactRef and an optional inclusive line range."""

    artifact_id: NonEmptyStr
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    start_char: int = Field(default=0, ge=0, description="Zero-based character offset within the selected physical lines.")
    end_char: int | None = Field(default=None, ge=1, description="Exclusive character end within the selected physical lines.")


class ReadArtifactTool:
    """Read a provided ArtifactRef after integrity verification."""

    name = "read_artifact"
    input_model = ReadArtifactInput
    model_guidance = (
        "If an artifact read is truncated, read a bounded start_line/end_line "
        "range. For a long line or JSON string, use start_char/end_char to read "
        "small character windows within those same lines (zero-based, end exclusive). "
        "Keep the line range fixed while paging characters. Do not repeat an "
        "unbounded read. The full frozen file is integrity-checked before any range is returned."
    )

    def __init__(self, reader: RegisteredArtifactReader) -> None:
        self.reader = reader

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(ReadArtifactInput, arguments)
        value = self.reader.read_text(
            args.artifact_id, start_line=args.start_line, end_line=args.end_line,
            start_char=args.start_char, end_char=args.end_char,
        )
        return ToolObservation(
            summary=f"Read registered Artifact {args.artifact_id}",
            value=value,
            memory_updates={
                "read_artifact_ids": _remember(
                    state,
                    "read_artifact_ids",
                    args.artifact_id,
                ),
            },
        )
