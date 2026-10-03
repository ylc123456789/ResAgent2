"""Search receipt and individually registered bibliographic sources."""

from __future__ import annotations

import hashlib
import json
from typing import cast

from pydantic import BaseModel, Field

from resagent2_contracts import ArtifactCandidate
from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import NonEmptyStr, RuntimeModel
from resagent2_components.artifacts import ArtifactRegistrationPort, RegisteredArtifactReader
from resagent2_components.literature import LiteratureSearchBackend, LiteratureSearchError, render_paper


class LiteratureSearchToolInput(RuntimeModel):
    query: NonEmptyStr = Field(description=(
        "Plain keywords or double-quoted phrases; do not use provider-specific "
        "field prefixes or Boolean operators."
    ))
    max_results: int = Field(default=10, ge=1, le=20)
    start_year: int | None = Field(default=None, ge=1900, le=2100)
    end_year: int | None = Field(default=None, ge=1900, le=2100)


def literature_outputs(state: AgentState, refs) -> dict:
    """Return registered outputs for handoff; registration is not reading."""
    return {"literature_output_artifact_ids": list(dict.fromkeys([
        *state.memory.get("literature_output_artifact_ids", []),
        *[ref.id for ref in refs],
    ]))}


class LiteratureSearchTool:
    """Register individual paper records and a separate search receipt."""

    name = "literature_search"
    input_model = LiteratureSearchToolInput
    model_guidance = (
        "Search returns separately registered papers and short abstract previews. "
        "The search receipt records the query/results; it is not a paper. "
        "Read a paper's artifact_id for its saved abstract. For needed details, "
        "use fetch_literature_fulltext with that paper_artifact_id, then read "
        "the returned fulltext artifact. A preview or abstract is not full text."
    )

    def __init__(self, backend: LiteratureSearchBackend, register: ArtifactRegistrationPort) -> None:
        self.backend = backend
        self.register = register

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(LiteratureSearchToolInput, arguments)
        failure = None
        try:
            papers = self.backend.search(
                args.query, max_results=args.max_results,
                start_year=args.start_year, end_year=args.end_year,
            )
        except LiteratureSearchError as error:
            papers, failure = [], str(error)

        registered = self.register.list_artifacts(run_id=state.run_id)
        reader = RegisteredArtifactReader(registered, run_id=state.run_id)
        existing = [ref for ref in registered if ref.kind == "literature_paper"]
        refs, brief = [], []
        seen = set()
        for paper in papers:
            if paper.key in seen:
                continue
            seen.add(paper.key)
            data = paper.model_dump(mode="json")
            ref = next((item for item in reversed(existing)
                        if item.metadata.get("paper_key") == paper.key
                        and item.metadata.get("paper") == data), None)
            if ref is not None:
                reader.verify(ref.id)
            else:
                token = hashlib.sha256(paper.key.encode()).hexdigest()[:16]
                ref = self.register.register_scientific(
                    ArtifactCandidate(
                        kind="literature_paper", path=f"paper_{token}.md",
                        media_type="text/markdown", summary=f"{paper.title} — metadata and abstract",
                        metadata={"paper_key": paper.key, "paper": paper.model_dump(mode="json")},
                        content=render_paper(paper),
                    ), run_id=state.run_id, session_id=state.session_id,
                )
                existing.append(ref)
            refs.append(ref)
            brief.append({
                "artifact_id": ref.id, **paper.model_dump(mode="json"),
                "abstract": paper.abstract[:200],
                "abstract_truncated": len(paper.abstract) > 200,
            })

        receipt = {
            **args.model_dump(mode="json"),
            "status": "failed" if failure else ("results" if refs else "empty"),
            "paper_artifact_ids": [ref.id for ref in refs],
            "sources": [item["source_url"] for item in brief],
            "error": failure,
        }
        artifact = self.register.register_scientific(
            ArtifactCandidate(
                kind="literature_search", path="literature_search.json",
                media_type="application/json", summary=f"Literature search: {args.query}",
                content=json.dumps(receipt, ensure_ascii=False, indent=2),
            ), run_id=state.run_id, session_id=state.session_id,
        )
        return ToolObservation(
            ok=failure is None,
            summary=failure or f"Found {len(refs)} papers for {args.query!r}",
            value={"artifact": artifact.model_dump(mode="json", exclude={"metadata"}),
                   "papers": brief, "status": receipt["status"], "error": failure},
            memory_updates=literature_outputs(state, [*refs, artifact]),
        )
