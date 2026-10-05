"""Search receipt and individually registered bibliographic sources."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, cast

from pydantic import BaseModel, Field, model_validator

from resagent2_contracts import ArtifactCandidate
from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import NonEmptyStr, RuntimeModel
from resagent2_components.artifacts import ArtifactRegistrationPort, RegisteredArtifactReader
from resagent2_components.literature import LiteratureSearchBackend, LiteratureSearchError, render_paper


class LiteratureSearchToolInput(RuntimeModel):
    query: NonEmptyStr = Field(description=(
        "Plain keywords or double-quoted phrases; do not use provider-specific "
        "field prefixes or Boolean operators. Escape literal double quotes and backslashes."
    ))
    source: Literal["auto", "arxiv", "openalex"] = "auto"
    scope: Literal["topic", "title"] = Field(
        default="topic",
        description=("topic searches title/abstract keywords or phrases; title treats "
                     "the complete query as one title phrase, with or without outer "
                     "double quotes. Check returned titles, authors and identifiers; "
                     "matching is not guaranteed exact or unique."),
    )
    page: int = Field(default=1, ge=1)
    max_results: int = Field(default=5, ge=1, le=20)
    start_year: int | None = Field(default=None, ge=1900, le=2100)
    end_year: int | None = Field(default=None, ge=1900, le=2100)

    @model_validator(mode="after")
    def validate_search_bounds(self):
        if self.start_year is not None and self.end_year is not None and self.start_year > self.end_year:
            raise ValueError("start_year must not exceed end_year")
        if self.source == "auto" and self.page > 1:
            raise ValueError("pagination requires an explicit source")
        return self


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
        "Use short keywords or quoted phrases for scope='topic'. For a named paper, "
        "scope='title' searches the complete query as one title phrase; outer double "
        "quotes are optional. Verify returned titles, authors and identifiers. "
        "An empty result does not prove the paper is absent; decide whether to revise "
        "the query or source based on the goal and receipt. "
        "Start with a small max_results (default 5). "
        "source='auto' chooses an available source on page 1; select arxiv or openalex "
        "explicitly to switch sources or paginate. The receipt gives the executed query, "
        "source attempts, and next_request with the same source and filters. "
        "OpenAlex numbered pages cover at most 10000 records. A missing next_request does not establish exhaustion when total_results is unknown or a paging limit is reached. "
        "Papers have separate artifact_ids and 500-character abstract previews; reused "
        "marks an unchanged registered snapshot. Read a paper's artifact_id for the "
        "complete saved abstract. For needed details, fetch_literature_fulltext with "
        "paper_artifact_id, then read the returned fulltext artifact. A search receipt "
        "is not a paper, and a preview or abstract is not full text."
    )

    def __init__(self, backend: LiteratureSearchBackend, register: ArtifactRegistrationPort) -> None:
        self.backend = backend
        self.register = register

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(LiteratureSearchToolInput, arguments)
        failure = None
        error_type = retry_after = None
        source, executed_query, page = None, None, args.page
        next_page = total_results = None
        source_attempts = []
        try:
            result = self.backend.search(
                args.query, max_results=args.max_results,
                start_year=args.start_year, end_year=args.end_year,
                source=args.source, scope=args.scope, page=args.page,
            )
            papers = result.papers
            source, executed_query, page = result.source, result.executed_query, result.page
            next_page, total_results = result.next_page, result.total_results
            source_attempts = result.source_attempts
        except LiteratureSearchError as error:
            papers, failure = [], str(error)
            error_type, retry_after = error.error_type, error.retry_after
            source_attempts = error.source_attempts
        next_request = ({**args.model_dump(mode="json"), "source": source, "page": next_page}
                        if next_page is not None else None)
        search_details = {
            "source": source, "executed_query": executed_query, "page": page,
            "source_attempts": source_attempts, "total_results": total_results,
            "next_request": next_request, "error_type": error_type, "retry_after": retry_after,
        }

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
            reused = ref is not None
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
                "artifact_id": ref.id, "reused": reused, **paper.model_dump(mode="json"),
                "abstract": paper.abstract[:500],
                "abstract_truncated": len(paper.abstract) > 500,
            })

        receipt = {
            **args.model_dump(mode="json"),
            "requested_source": args.source, **search_details,
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
                   "papers": brief, "status": receipt["status"], "error": failure,
                   **search_details},
            memory_updates=literature_outputs(state, [*refs, artifact]),
        )
