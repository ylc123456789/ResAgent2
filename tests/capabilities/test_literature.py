"""Tests for the literature search capability (DEVELOPMENT_PLAN §7.3)."""

from datetime import UTC, datetime, date
from urllib.parse import parse_qs, urlsplit

import httpx
import json

from resagent2_orchestrator import ArtifactRegistry
import pytest

from resagent2_components import (
    ArxivLiteratureBackend,
    LiteraturePaper,
    LiteratureSearchError,
)
from resagent2_capabilities import (
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


@pytest.fixture(autouse=True)
def isolated_http(monkeypatch, request):
    if request.node.name == "test_arxiv_backend_live_smoke":
        return
    from resagent2_components.literature import (
        _http as _literature_http,
        backends as literature,
    )

    clock = [0.0]
    monkeypatch.setattr(_literature_http.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        _literature_http.time, "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    monkeypatch.setattr(
        literature, "_ARXIV_HTTP",
        _literature_http.LiteratureHTTP("arXiv", interval_seconds=3),
    )


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
    """Exercise actual freezing while keeping the Run store out of tool tests."""

    def __init__(self, root) -> None:
        self.registry = ArtifactRegistry(root)
        self.refs = {}
        self.last_run_id = self.last_session_id = self.last_candidate = None

    def register_scientific(self, candidate, *, run_id, session_id, content_bytes=None):
        self.last_run_id, self.last_session_id = run_id, session_id
        self.last_candidate = candidate
        ref = self.registry.register_scientific(
            candidate, run_id=run_id, session_id=session_id, content_bytes=content_bytes,
        )
        self.refs[ref.id] = ref
        return ref

    def list_artifacts(self, *, run_id):
        return [ref for ref in self.refs.values() if ref.run_id == run_id]

    def resolve(self, artifact_id, *, run_id):
        ref = self.refs.get(artifact_id)
        return ref if ref and ref.run_id == run_id else None


def paper(paper_id: str) -> LiteraturePaper:
    return LiteraturePaper(
        paper_id=paper_id,
        title=f"Title {paper_id}",
        authors=["Alice"],
        published_at=date(2024, 1, 1),
        abstract="An abstract.",
        source_url=f"https://arxiv.org/abs/{paper_id}",
    )


def test_tool_returns_separate_papers_and_search_receipt(tmp_path) -> None:
    register = _FakeRegister(tmp_path)
    tool = LiteratureSearchTool(_FakeBackend([paper("2301.00001"), paper("2301.00002")]), register)
    observation = tool.execute(state(), LiteratureSearchToolInput(query="electron", max_results=5))
    value = observation.value
    assert value["artifact"]["kind"] == "literature_search"
    assert len(value["papers"]) == 2
    ids = [item["artifact_id"] for item in value["papers"]]
    assert len(set(ids)) == 2
    assert all(register.refs[key].kind == "literature_paper" for key in ids)
    assert register.last_run_id == "run_example"
    assert register.last_session_id == "session_sci"
    receipt = json.loads(register.last_candidate.content)
    assert receipt["paper_artifact_ids"] == ids
    assert "An abstract" not in register.last_candidate.content
    assert "id" not in register.last_candidate.model_dump()
    assert "sha256" not in register.last_candidate.model_dump()


def test_tool_reuses_papers_and_tracks_outputs_not_read_status(tmp_path) -> None:
    register = _FakeRegister(tmp_path)
    tool = LiteratureSearchTool(_FakeBackend([paper("1")]), register)
    observation = tool.execute(state(), LiteratureSearchToolInput(query="x", max_results=1))
    first_paper = observation.value["papers"][0]["artifact_id"]
    assert "literature_artifact_ids" not in observation.memory_updates
    original = state(memory=observation.memory_updates)
    second = tool.execute(original, LiteratureSearchToolInput(query="y", max_results=1))
    assert second.value["papers"][0]["artifact_id"] == first_paper
    assert len([ref for ref in register.refs.values() if ref.kind == "literature_paper"]) == 1
    assert len(second.memory_updates["literature_output_artifact_ids"]) == 3


def test_tool_keeps_preview_separate_from_frozen_abstract(tmp_path) -> None:
    full_abstract = "Scientific evidence. " * 100
    result_paper = paper("1").model_copy(update={"abstract": full_abstract})
    register = _FakeRegister(tmp_path)
    original = state()
    before = original.model_copy(deep=True)
    observation = LiteratureSearchTool(_FakeBackend([result_paper]), register).execute(
        original, LiteratureSearchToolInput(query="evidence"),
    )
    brief = observation.value["papers"][0]
    assert brief["abstract"] == full_abstract[:200]
    assert brief["abstract_truncated"] is True
    ref = register.refs[brief["artifact_id"]]
    assert ref.metadata["paper"]["abstract"] == full_abstract
    assert len(observation.memory_updates["literature_output_artifact_ids"]) == 2
    assert original == before


@pytest.mark.parametrize("failure", [False, True])
def test_empty_or_failed_search_is_a_receipt_not_a_paper(tmp_path, failure):
    class Backend:
        def search(self, *args, **kwargs):
            if failure:
                raise LiteratureSearchError("provider unavailable")
            return []
    register = _FakeRegister(tmp_path)
    result = LiteratureSearchTool(Backend(), register).execute(
        state(), LiteratureSearchToolInput(query="missing"),
    )
    assert result.value["status"] == ("failed" if failure else "empty")
    assert result.ok is (not failure)
    assert result.value["papers"] == []
    assert [ref.kind for ref in register.refs.values()] == ["literature_search"]


def test_tool_forwards_query_and_bounds(tmp_path) -> None:
    backend = _FakeBackend([paper("1")])
    LiteratureSearchTool(backend, _FakeRegister(tmp_path)).execute(
        state(), LiteratureSearchToolInput(
            query="graph neural networks", max_results=7, start_year=2020, end_year=2024,
        ),
    )
    assert backend.last_kwargs == {
        "query": "graph neural networks", "max_results": 7, "start_year": 2020, "end_year": 2024,
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

    def _request(self, url: str) -> httpx.Response:
        self.last_url = url
        return httpx.Response(200, content=self._body, request=httpx.Request("GET", url))


def test_arxiv_backend_parses_deduplicates_and_normalizes() -> None:
    backend = _FakeArxivBackend(ARXIV_ATOM.encode("utf-8"))
    papers = backend.search("electron", max_results=10)

    assert [p.paper_id for p in papers] == ["2301.00001v2", "2301.00002v1"]
    first = papers[0]
    assert first.title == "First Paper"
    assert first.authors == ["Alice", "Bob"]
    assert first.published_at == date(2023, 1, 5)
    assert first.abstract == "A line break abstract that keeps going."
    assert first.source_url == "https://arxiv.org/abs/2301.00001v2"
    assert first.pdf_url == "https://arxiv.org/pdf/2301.00001v2"


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


@pytest.mark.parametrize("query,expected", [
    ("electron", 'all:"electron"'),
    ("graph neural networks", 'all:"graph" AND all:"neural" AND all:"networks"'),
    ('"temperature scaling" calibration', 'all:"temperature scaling" AND all:"calibration"'),
    ("C++ O'Neill", "all:\"C++\" AND all:\"O'Neill\""),
    (r'"quote \"inside\"" "path\\name"', r'all:"quote \"inside\"" AND all:"path\\name"'),
    ("a OR ti:b", 'all:"a" AND all:"OR" AND all:"ti:b"'),
])
def test_arxiv_translates_keywords_and_phrases_without_query_operators(query, expected):
    backend = _FakeArxivBackend(ARXIV_ATOM.encode())
    backend.search(query, max_results=3, start_year=2020, end_year=2024)
    params = parse_qs(urlsplit(backend.last_url).query)
    assert params["search_query"] == [
        expected + " AND submittedDate:[202001010000 TO 202412312359]"
    ]
    assert params["max_results"] == ["3"]


@pytest.mark.parametrize("query", ['"unclosed', '""', " \t ", 'keyword ""'])
def test_arxiv_rejects_malformed_keyword_input_before_http(query):
    backend = _FakeArxivBackend(ARXIV_ATOM.encode())
    with pytest.raises(LiteratureSearchError, match="keywords"):
        backend.search(query, max_results=3)
    assert not hasattr(backend, "last_url")


def test_arxiv_backend_raises_clear_error_instead_of_empty_result() -> None:
    class Failing(ArxivLiteratureBackend):
        def _request(self, url: str) -> httpx.Response:
            raise TimeoutError("connection timed out")

    backend = Failing(max_retries=2)
    with pytest.raises(LiteratureSearchError, match="failed after 2 attempts"):
        backend.search("electron", max_results=5)


def test_arxiv_backend_rejects_invalid_xml() -> None:
    backend = _FakeArxivBackend(b"not xml")
    with pytest.raises(LiteratureSearchError, match="invalid XML"):
        backend.search("electron", max_results=5)


@pytest.mark.parametrize("body", [
    b"<html>service error</html>",
    b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors</id></entry></feed>',
    b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2301.00001</id></entry></feed>',
])
def test_arxiv_invalid_feed_is_not_an_empty_search(body):
    with pytest.raises(LiteratureSearchError):
        _FakeArxivBackend(body).search("x", max_results=1)


def test_arxiv_request_identifies_application(monkeypatch):
    from resagent2_components.literature import backends as literature

    requests = []
    def open_request(request, *, timeout):
        requests.append((request, timeout))
        return httpx.Response(200, content=ARXIV_ATOM.encode(), request=request)
    monkeypatch.setattr(literature, "send_request", open_request)
    ArxivLiteratureBackend(timeout_seconds=7).search("x", max_results=1)
    request, timeout = requests[0]
    assert request.headers.get("User-agent").startswith("ResAgent2/")
    assert timeout == 7


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
