"""Tests for the literature search capability (DEVELOPMENT_PLAN §7.3)."""

from datetime import UTC, datetime, date

import pytest

from resagent2_capabilities import (
    ArxivLiteratureBackend,
    LiteraturePaper,
    LiteratureSearchError,
    LiteratureSearchTool,
    LiteratureSearchToolInput,
)
from resagent2_contracts import (
    AgentOwner,
    ArtifactRef,
    RunId,
    SessionId,
    SessionStatus,
    TaskId,
)
from resagent2_runtime import AgentState, ToolObservation


NOW = datetime(2026, 8, 28, tzinfo=UTC)


def state(*, memory: dict | None = None) -> AgentState:
    return AgentState(
        session_id="session_sci",
        agent_name="scientific",
        owner=AgentOwner.SCIENTIFIC,
        run_id="run_example",
        task_id="task_sci",
        attempt_number=1,
        status=SessionStatus.ACTIVE,
        memory=memory or {},
        created_at=NOW,
        updated_at=NOW,
    )


class _FakeBackend:
    """Returns fixed papers, recording the last query arguments."""

    def __init__(self, papers: list[LiteraturePaper]) -> None:
        self._papers = papers
        self.last_kwargs: dict | None = None

    def search(self, query, *, max_results, start_year=None, end_year=None):
        self.last_kwargs = {
            "query": query,
            "max_results": max_results,
            "start_year": start_year,
            "end_year": end_year,
        }
        return self._papers


class _FakeRegister:
    """Returns a fixed ArtifactRef, recording the provenance it was given."""

    def __init__(self) -> None:
        self.last_run_id: RunId | None = None
        self.last_session_id: SessionId | None = None
        self.last_candidate = None

    def register_scientific(self, candidate, *, run_id, session_id) -> ArtifactRef:
        self.last_run_id = run_id
        self.last_session_id = session_id
        self.last_candidate = candidate
        return ArtifactRef(
            id="artifact_lit",
            kind="literature_search",
            producer=AgentOwner.SCIENTIFIC,
            run_id=run_id,
            session_id=session_id,
            uri="file:///artifacts/lit.json",
            sha256="0" * 64,
            media_type="application/json",
            summary="literature results",
        )


def paper(paper_id: str) -> LiteraturePaper:
    return LiteraturePaper(
        paper_id=paper_id,
        title=f"Title {paper_id}",
        authors=["Alice"],
        published_at=date(2024, 1, 1),
        abstract="An abstract.",
        source_url=f"https://arxiv.org/abs/{paper_id}",
    )


def test_tool_returns_artifact_and_papers_with_session_provenance() -> None:
    register = _FakeRegister()
    tool = LiteratureSearchTool(
        _FakeBackend([paper("2301.00001")]), register
    )
    observation = tool.execute(
        state(),
        LiteratureSearchToolInput(query="electron", max_results=5),
    )

    assert isinstance(observation, ToolObservation)
    value = observation.value
    assert value["artifact"]["id"] == "artifact_lit"
    assert len(value["papers"]) == 1
    assert value["papers"][0]["paper_id"] == "2301.00001"
    # Tool must pass run/session provenance, not assign its own id/hash.
    assert register.last_run_id == "run_example"
    assert register.last_session_id == "session_sci"
    assert "id" not in register.last_candidate.model_dump()
    assert "sha256" not in register.last_candidate.model_dump()


def test_tool_records_observed_artifact_in_memory() -> None:
    register = _FakeRegister()
    tool = LiteratureSearchTool(_FakeBackend([paper("1")]), register)
    observation = tool.execute(
        state(), LiteratureSearchToolInput(query="x", max_results=1)
    )
    assert observation.memory_updates["literature_artifact_ids"] == ["artifact_lit"]

    # A second search must not duplicate the observed id.
    second = tool.execute(
        state(memory={"literature_artifact_ids": ["artifact_lit"]}),
        LiteratureSearchToolInput(query="y", max_results=1),
    )
    assert second.memory_updates["literature_artifact_ids"] == ["artifact_lit"]


def test_tool_forwards_query_and_bounds() -> None:
    backend = _FakeBackend([paper("1")])
    tool = LiteratureSearchTool(backend, _FakeRegister())
    tool.execute(
        state(),
        LiteratureSearchToolInput(
            query="graph neural networks",
            max_results=7,
            start_year=2020,
            end_year=2024,
        ),
    )
    assert backend.last_kwargs == {
        "query": "graph neural networks",
        "max_results": 7,
        "start_year": 2020,
        "end_year": 2024,
    }


ARXIV_ATOM = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2301.00001v2</id>
    <title>  First Paper   </title>
    <author><name>Alice</name></author>
    <author><name>Bob</name></author>
    <published>2023-01-05T12:00:00Z</published>
    <summary>  A line break
      abstract that keeps going.  </summary>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2301.00001v2</id>
    <title>Duplicate</title>
    <author><name>Alice</name></author>
    <published>2023-01-05T12:00:00Z</published>
    <summary>duplicate</summary>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2301.00002v1</id>
    <title>Second</title>
    <published>2023-01-06T12:00:00Z</published>
    <summary>short</summary>
  </entry>
</feed>
"""


class _FakeArxivBackend(ArxivLiteratureBackend):
    """Override _request so no network is touched, keeping the retry logic."""

    def __init__(self, body: bytes) -> None:
        super().__init__()
        self._body = body

    def _request(self, url: str) -> bytes:
        self.last_url = url
        return self._body


def test_arxiv_backend_parses_deduplicates_and_normalizes() -> None:
    backend = _FakeArxivBackend(ARXIV_ATOM.encode("utf-8"))
    papers = backend.search("electron", max_results=10)

    assert [p.paper_id for p in papers] == ["2301.00001", "2301.00002"]
    first = papers[0]
    assert first.title == "First Paper"
    assert first.authors == ["Alice", "Bob"]
    assert first.published_at == date(2023, 1, 5)
    assert first.abstract == "A line break abstract that keeps going."
    assert first.source_url == "https://arxiv.org/abs/2301.00001"


def test_arxiv_backend_truncates_abstract() -> None:
    long_abstract = "x" * 5000
    atom = ARXIV_ATOM.replace(
        "short", long_abstract
    )
    backend = _FakeArxivBackend(atom.encode("utf-8"))
    backend.max_abstract_chars = 100
    papers = backend.search("electron", max_results=10)

    assert len(papers[-1].abstract) == 100


def test_arxiv_backend_builds_year_bounded_query() -> None:
    backend = _FakeArxivBackend(ARXIV_ATOM.encode("utf-8"))
    backend.search("electron", max_results=5, start_year=2020, end_year=2024)

    assert "submittedDate" in backend.last_url
    assert "202001010000" in backend.last_url
    assert "202412312359" in backend.last_url


def test_arxiv_backend_raises_clear_error_instead_of_empty_result() -> None:
    class Failing(ArxivLiteratureBackend):
        def _request(self, url: str) -> bytes:
            raise TimeoutError("connection timed out")

    backend = Failing(max_retries=2)
    with pytest.raises(LiteratureSearchError, match="failed after 2 retries"):
        backend.search("electron", max_results=5)


def test_arxiv_backend_rejects_invalid_xml() -> None:
    backend = _FakeArxivBackend(b"not xml")
    with pytest.raises(LiteratureSearchError, match="invalid XML"):
        backend.search("electron", max_results=5)


@pytest.mark.skipif(
    not __import__("os").environ.get("RESAGENT2_LITERATURE_SMOKE"),
    reason="network smoke test is opt-in via RESAGENT2_LITERATURE_SMOKE",
)
def test_arxiv_backend_live_smoke() -> None:
    """Opt-in real network test; must not fake success on rate limit/timeout."""
    backend = ArxivLiteratureBackend(timeout_seconds=10, max_retries=2)
    papers = backend.search("graph neural network", max_results=3)
    assert len(papers) >= 1
    assert all(p.title and p.source_url for p in papers)
