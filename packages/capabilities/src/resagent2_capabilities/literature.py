"""Literature search capability: a backend Protocol, an arXiv backend, and a Tool.

The backend is injected by the composition root, so the Scientific Agent never
imports an arXiv SDK. A successful search is normalized, deduplicated and
truncated here, then handed to an injected ``ArtifactRegistrationPort`` that
freezes it with the current run/session provenance. The Tool never assigns an
ArtifactId or hash.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from defusedxml import ElementTree

from pydantic import BaseModel, Field

from resagent2_contracts import (
    ArtifactCandidate,
    ArtifactRef,
    RunId,
    SessionId,
)
from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import NonEmptyStr, RuntimeModel


class LiteratureSearchError(RuntimeError):
    """Raised when a backend cannot return a normalized result."""


class LiteraturePaper(RuntimeModel):
    """One normalized paper; raw backend responses never reach the prompt."""

    paper_id: NonEmptyStr
    title: NonEmptyStr
    authors: list[NonEmptyStr] = Field(default_factory=list)
    published_at: date | None = None
    abstract: str = ""
    source_url: NonEmptyStr


class LiteratureSearchBackend(Protocol):
    """Provider-neutral literature search returning normalized papers."""

    def search(
        self,
        query: str,
        *,
        max_results: int,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> list[LiteraturePaper]:
        """Return normalized, deduplicated, truncated papers for one query."""


class ArtifactRegistrationPort(Protocol):
    """Scientific Tool seam for freezing results via the ResAgent Registry.

    The composition root adapts the orchestrator ArtifactRegistry to this shape;
    capabilities must not import the orchestrator.
    """

    def register_scientific(
        self,
        candidate: ArtifactCandidate,
        *,
        run_id: RunId,
        session_id: SessionId,
    ) -> ArtifactRef:
        """Freeze one candidate with session provenance and return its Ref."""


_ATOM = "{http://www.w3.org/2005/Atom}"


class ArxivLiteratureBackend:
    """Query the arXiv API and normalize Atom entries into LiteraturePaper.

    Network errors, rate limits and timeouts are retried with exponential
    backoff and then raised as ``LiteratureSearchError``; they are never
    silently converted into an empty result.
    """

    _endpoint = "https://export.arxiv.org/api/query"

    def __init__(
        self,
        *,
        timeout_seconds: int = 30,
        max_retries: int = 3,
        max_abstract_chars: int = 2_000,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_abstract_chars = max_abstract_chars

    def search(
        self,
        query: str,
        *,
        max_results: int,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> list[LiteraturePaper]:
        params = {
            "search_query": self._search_query(query, start_year, end_year),
            "start": 0,
            "max_results": max_results,
        }
        url = f"{self._endpoint}?{urlencode(params)}"
        body = self._fetch(url)
        return self._parse(body)

    @staticmethod
    def _search_query(
        query: str, start_year: int | None, end_year: int | None
    ) -> str:
        """Append a submittedDate range only when a year bound is present."""
        terms = [f"all:{query}"]
        if start_year is not None and end_year is not None:
            terms.append(
                f"submittedDate:[{start_year}01010000 TO {end_year}12312359]"
            )
        elif start_year is not None:
            terms.append(f"submittedDate:[{start_year}01010000 TO 999912312359]")
        elif end_year is not None:
            terms.append(f"submittedDate:[000001010000 TO {end_year}12312359]")
        return " AND ".join(terms)

    def _request(self, url: str) -> bytes:
        """One raw HTTP request; overridable in tests to avoid the network."""
        with urlopen(url, timeout=self.timeout_seconds) as response:
            return response.read()

    def _fetch(self, url: str) -> bytes:
        delay = 1.0
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self._request(url)
            except (HTTPError, URLError, TimeoutError) as error:
                last_error = error
                if attempt < self.max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
        raise LiteratureSearchError(
            f"arXiv request failed after {self.max_retries} retries: {last_error}"
        ) from last_error

    def _parse(self, body: bytes) -> list[LiteraturePaper]:
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as error:
            raise LiteratureSearchError(f"arXiv returned invalid XML: {error}") from error

        papers: list[LiteraturePaper] = []
        seen: set[str] = set()
        for entry in root.findall(f"{_ATOM}entry"):
            paper = self._paper(entry)
            if paper.paper_id in seen:
                continue
            seen.add(paper.paper_id)
            papers.append(paper)
        return papers

    def _paper(self, entry: ElementTree.Element) -> LiteraturePaper:
        id_url = self._text(entry, "id")
        paper_id = self._paper_id(id_url)
        abstract = self._text(entry, "summary")
        abstract = " ".join(abstract.split())
        if len(abstract) > self.max_abstract_chars:
            abstract = abstract[: self.max_abstract_chars]
        authors = [
            name.text.strip()
            for author in entry.findall(f"{_ATOM}author")
            for name in author.findall(f"{_ATOM}name")
            if name.text and name.text.strip()
        ]
        published = self._text(entry, "published")
        published_at = None
        if published:
            try:
                published_at = date.fromisoformat(published[:10])
            except ValueError:
                published_at = None
        return LiteraturePaper(
            paper_id=paper_id,
            title=" ".join(self._text(entry, "title").split()),
            authors=authors,
            published_at=published_at,
            abstract=abstract,
            source_url=f"https://arxiv.org/abs/{paper_id}",
        )

    @staticmethod
    def _text(entry: ElementTree.Element, tag: str) -> str:
        node = entry.find(f"{_ATOM}{tag}")
        return node.text or "" if node is not None else ""

    @staticmethod
    def _paper_id(id_url: str) -> str:
        """Turn an arXiv id URL like ``.../abs/2301.12345v2`` into ``2301.12345``."""
        fragment = id_url.rsplit("/", 1)[-1]
        return fragment.split("v", 1)[0] if "v" in fragment else fragment


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
            path="literature_search.json",
            media_type="application/json",
            summary=f"Literature search: {args.query}",
            metadata={"papers": [paper.model_dump(mode="json") for paper in papers]},
        )
        artifact = self.register.register_scientific(
            candidate,
            run_id=state.run_id,
            session_id=state.session_id,
        )
        observed = list(state.memory.get("literature_artifact_ids", []))
        if artifact.id not in observed:
            observed.append(artifact.id)
        return ToolObservation(
            summary=f"Found {len(papers)} papers for {args.query!r}",
            value={
                "artifact": artifact.model_dump(mode="json"),
                "papers": [paper.model_dump(mode="json") for paper in papers],
            },
            memory_updates={"literature_artifact_ids": observed},
        )
