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


class ReadArtifactTool:
    """Read a provided ArtifactRef after integrity verification."""

    name = "read_artifact"
    input_model = ReadArtifactInput
    model_guidance = (
        "If an artifact read is truncated, read a bounded start_line/end_line "
        "range; do not repeat the same unbounded read. The full frozen file "
        "is integrity-checked before any range is returned."
    )

    def __init__(self, reader: RegisteredArtifactReader) -> None:
        self.reader = reader

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(ReadArtifactInput, arguments)
        value = self.reader.read_text(
            args.artifact_id, start_line=args.start_line, end_line=args.end_line,
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
