"""OpenAlex bibliographic search implementing the existing literature port."""

from __future__ import annotations

import json
from datetime import date
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ._literature_http import USER_AGENT, LiteratureHTTP, LiteratureSearchError
from .literature import LiteraturePaper


_OPENALEX_HTTP = LiteratureHTTP("OpenAlex", interval_seconds=1.0)


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
        request = Request(url, headers=headers)
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return response.read()

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
