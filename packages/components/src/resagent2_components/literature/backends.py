"""Normalized bibliographic records, peer backends and artifact presentation."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Protocol
from urllib.parse import urlencode
import httpx

from defusedxml import ElementTree
from pydantic import Field, ValidationError

from resagent2_runtime.models import NonEmptyStr, RuntimeModel
from resagent2_runtime.http import send_request
from ..text import wrap_text_lines
from ._http import (
    USER_AGENT, LiteratureHTTP, LiteratureSearchError, LiteratureUnavailableError,
)

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV_HTTP = LiteratureHTTP("arXiv", interval_seconds=3.0)
_OPENALEX_HTTP = LiteratureHTTP("OpenAlex", interval_seconds=1.0)


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


class ArxivLiteratureBackend:
    """Query the arXiv API and normalize Atom entries into LiteraturePaper.

    Requests are serialized and spaced by at least three seconds in-process.
    Rate limits trigger cooldown immediately; transient errors have bounded
    retries. Failures never become empty search results.
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
        self.max_retries = max_retries  # Historical name: total HTTP attempts.
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
        return send_request(httpx.Request("GET", url, headers={"User-Agent": USER_AGENT}),
                            timeout=self.timeout_seconds).content

    def _fetch(self, url: str) -> bytes:
        return _ARXIV_HTTP.fetch(
            lambda: self._request(url), max_attempts=self.max_retries
        )

    def _parse(self, body: bytes) -> list[LiteraturePaper]:
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as error:
            raise LiteratureSearchError(f"arXiv returned invalid XML: {error}") from error
        if root.tag != f"{_ATOM}feed":
            raise LiteratureSearchError("arXiv returned a non-Atom response")

        papers: list[LiteraturePaper] = []
        seen: set[str] = set()
        for entry in root.findall(f"{_ATOM}entry"):
            if "/abs/" not in self._text(entry, "id"):
                raise LiteratureSearchError("arXiv returned an error or invalid entry")
            try:
                paper = self._paper(entry)
            except ValidationError as error:
                raise LiteratureSearchError("arXiv returned an invalid paper") from error
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


class MultiSourceLiteratureBackend:
    """Peer sources: keep the last successful one, switch on unavailability.

    Each search traverses the configured sources at most once. The initial
    order is a composition choice, not a primary/backup role. Only an index
    is remembered in this instance; cooldown remains in the HTTP backends.
    """

    def __init__(self, *backends: LiteratureSearchBackend) -> None:
        if not backends:
            raise ValueError("at least one literature backend is required")
        self.backends = backends
        self._current_index = 0

    def search(
        self,
        query: str,
        *,
        max_results: int,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> list[LiteraturePaper]:
        bounds = dict(max_results=max_results, start_year=start_year, end_year=end_year)
        errors: list[str] = []
        start = self._current_index
        for offset in range(len(self.backends)):
            index = (start + offset) % len(self.backends)
            backend = self.backends[index]
            try:
                papers = backend.search(query, **bounds)
            except LiteratureUnavailableError as error:
                detail = f"{type(backend).__name__}: {error}"
                errors.append(detail)
                logging.getLogger(__name__).warning(
                    "Literature source unavailable (%s)", detail
                )
                continue
            self._current_index = index
            return papers
        raise LiteratureUnavailableError(
            "All literature sources unavailable: " + "; ".join(errors)
        )


def render_literature(papers: list[LiteraturePaper]) -> str:
    """Present retrieved records by paper, without LLM summaries or new facts."""
    sections = [
        "# Literature search results",
        "Retrieved bibliographic records and abstracts, not paper full text or "
        "independently verified findings. Read each relevant entry before using "
        "its contents; an abstract supports only abstract-level claims.",
    ]
    if not papers:
        sections.append("No papers returned.")
    for index, paper in enumerate(papers, start=1):
        sections.append(wrap_text_lines(
            f"## Paper {index}: {paper.title}\n\n"
            f"Paper ID: {paper.paper_id}\n"
            f"Source: {paper.source_url}\n"
            f"Published: {paper.published_at or '(not supplied)'}\n"
            f"Authors: {', '.join(paper.authors) or '(not supplied)'}\n\n"
            f"### Retrieved abstract\n\n{paper.abstract or '(not supplied)'}"
        ))
    return "\n\n".join(sections) + "\n"


class OpenAlexLiteratureBackend:
    """Normalize indexed records/abstracts, without fetching or inventing full text."""

    _endpoint = "https://api.openalex.org/works"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout_seconds: int = 30,
        max_retries: int = 3,
        max_abstract_chars: int = 2_000,
    ) -> None:
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries  # Total HTTP attempts, as in arXiv backend.
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
            "search": query,
            "per_page": max_results,
            "select": "id,display_name,publication_date,authorships,abstract_inverted_index",
        }
        filters = []
        if start_year is not None:
            filters.append(f"from_publication_date:{start_year}-01-01")
        if end_year is not None:
            filters.append(f"to_publication_date:{end_year}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        url = f"{self._endpoint}?{urlencode(params)}"
        body = _OPENALEX_HTTP.fetch(
            lambda: self._request(url), max_attempts=self.max_retries
        )
        return self._parse(body)

    def _request(self, url: str) -> bytes:
        headers = {"User-Agent": USER_AGENT}
        if self._api_key:
            # Never place credentials in URLs, artifacts, or model context.
            headers["Authorization"] = f"Bearer {self._api_key}"
        return send_request(httpx.Request("GET", url, headers=headers),
                            timeout=self.timeout_seconds).content

    def _parse(self, body: bytes) -> list[LiteraturePaper]:
        try:
            response = json.loads(body)
            if not isinstance(response, dict) or not isinstance(
                response.get("results"), list
            ):
                raise ValueError("missing results list")
            papers = {}
            for record in response["results"]:
                paper = self._paper(record)
                papers.setdefault(paper.paper_id, paper)
            return list(papers.values())
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise LiteratureSearchError(
                "OpenAlex returned invalid bibliographic data"
            ) from error

    def _paper(self, record: dict) -> LiteraturePaper:
        source_url = record["id"]
        prefix = "https://openalex.org/W"
        if not isinstance(source_url, str) or not source_url.startswith(prefix):
            raise ValueError("invalid OpenAlex work id")
        if not source_url[len(prefix):].isdigit():
            raise ValueError("invalid OpenAlex work id")
        published = record.get("publication_date")
        return LiteraturePaper(
            paper_id=f"openalex:{source_url.rsplit('/', 1)[-1]}",
            title=record["display_name"],
            authors=[
                entry["author"]["display_name"]
                for entry in (record.get("authorships") or [])
                if entry["author"].get("display_name")
            ],
            published_at=date.fromisoformat(published) if published else None,
            abstract=self._abstract(record.get("abstract_inverted_index")),
            source_url=source_url,
        )

    def _abstract(self, index: dict[str, list[int]] | None) -> str:
        if index is None:
            return ""
        words = {}
        for word, positions in index.items():
            if not isinstance(word, str) or not isinstance(positions, list):
                raise ValueError("invalid abstract index")
            for position in positions:
                if type(position) is not int or position < 0 or position in words:
                    raise ValueError("invalid abstract position")
                words[position] = word
        abstract = " ".join(words[position] for position in sorted(words))
        return abstract[:self.max_abstract_chars]
