"""Literature search tool; the composition root supplies its backend and registry."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import NonEmptyStr, RuntimeModel

from resagent2_contracts import ArtifactCandidate
from resagent2_components.artifacts import ArtifactRegistrationPort
from resagent2_components.literature import LiteratureSearchBackend, render_literature


class LiteratureSearchToolInput(RuntimeModel):
    """Bounded literature query for the Scientific Agent."""

    query: NonEmptyStr
    max_results: int = Field(default=10, ge=1, le=20)
    start_year: int | None = Field(default=None, ge=1900, le=2100)
    end_year: int | None = Field(default=None, ge=1900, le=2100)


class LiteratureSearchTool:
    """Search literature, then freeze the normalized result with provenance."""

    name = "literature_search"
    input_model = LiteratureSearchToolInput

    def __init__(
        self,
        backend: LiteratureSearchBackend,
        register: ArtifactRegistrationPort,
    ) -> None:
        self.backend = backend
        self.register = register

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(LiteratureSearchToolInput, arguments)
        papers = self.backend.search(
            args.query,
            max_results=args.max_results,
            start_year=args.start_year,
            end_year=args.end_year,
        )
        candidate = ArtifactCandidate(
            kind="literature_search",
            path="literature_search.md",
            media_type="text/markdown",
            summary=f"Literature search: {args.query}",
            metadata={"papers": [paper.model_dump(mode="json") for paper in papers]},
            content=render_literature(papers),
        )
        artifact = self.register.register_scientific(
            candidate,
            run_id=state.run_id,
            session_id=state.session_id,
        )
        observed = list(state.memory.get("literature_artifact_ids", []))
        if artifact.id not in observed:
            observed.append(artifact.id)
        # Bound the in-context summary: the full abstracts live in the frozen
        # artifact (read via read_artifact), so the required last_observation
        # context section never exceeds the context budget.
        brief = []
        for paper in papers:
            data = paper.model_dump(mode="json")
            data["abstract"] = (data.get("abstract") or "")[:200]
            brief.append(data)
        return ToolObservation(
            summary=f"Found {len(papers)} papers for {args.query!r}",
            value={
                "artifact": artifact.model_dump(mode="json", exclude={"metadata"}),
                "papers": brief,
            },
            memory_updates={
                "literature_artifact_ids": observed,
            },
        )
