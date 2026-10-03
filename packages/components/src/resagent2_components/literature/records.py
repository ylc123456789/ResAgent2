"""One bibliographic source and its deterministic, readable presentation."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlsplit

from pydantic import Field

from resagent2_runtime.models import NonEmptyStr, RuntimeModel
from ..text import wrap_text_lines


class LiteraturePaper(RuntimeModel):
    """Metadata and an abstract, never a claim to have retrieved full text."""

    paper_id: NonEmptyStr
    title: NonEmptyStr
    authors: list[NonEmptyStr] = Field(default_factory=list)
    published_at: date | None = None
    abstract: str = ""
    source_url: NonEmptyStr
    doi: str | None = None
    pdf_url: str | None = None

    @property
    def key(self) -> str:
        # An explicit arXiv version must not collapse into another version/DOI.
        if urlsplit(self.source_url).hostname in {"arxiv.org", "export.arxiv.org"}:
            return "arxiv:" + self.paper_id
        if self.doi:
            return "doi:" + self.doi.lower().removeprefix("https://doi.org/").removeprefix("http://doi.org/")
        return self.paper_id


def render_paper(paper: LiteraturePaper) -> str:
    """Preserve supplied source text; this is not an LLM-generated summary."""
    return wrap_text_lines(
        f"# {paper.title}\n\n"
        "Bibliographic record and provided abstract, not paper full text.\n"
        "Abstracts support only abstract-level claims.\n\n"
        f"Paper ID: {paper.paper_id}\n"
        f"Source: {paper.source_url}\n"
        f"DOI: {paper.doi or '(not supplied)'}\n"
        f"Published: {paper.published_at or '(not supplied)'}\n"
        f"Authors: {', '.join(paper.authors) or '(not supplied)'}\n"
        f"PDF URL: {paper.pdf_url or '(not supplied)'}\n\n"
        f"## Provided abstract\n\n{paper.abstract or '(not supplied)'}"
    ) + "\n"
