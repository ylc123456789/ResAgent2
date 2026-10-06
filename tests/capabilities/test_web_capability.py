"""Deterministic tests for Scientific web capabilities."""

from datetime import UTC, datetime
import hashlib
import json

from resagent2_contracts import AgentOwner, ArtifactRef, ArtifactCandidate, SessionStatus
from resagent2_orchestrator import ArtifactRegistry
from resagent2_components import (
    WebPage, WebSearchError, WebSearchItem, WebSearchResult,
    RegisteredArtifactReader,
)
from resagent2_capabilities import WebFetchInput, WebFetchTool, WebSearchInput, WebSearchTool
from resagent2_runtime import AgentState, ToolObservation


NOW = datetime(2026, 10, 6, tzinfo=UTC)


def _state():
    return AgentState(
        session_id="session_sci",
        agent_name="scientific",
        owner=AgentOwner.SCIENTIFIC,
        run_id="run_example",
        task_id="task_sci",
        attempt_number=1,
        status=SessionStatus.ACTIVE,
        memory={},
        created_at=NOW,
        updated_at=NOW,
    )


class _Register:
    def __init__(self, root):
        self.registry = ArtifactRegistry(root)
        self.refs = {}

    def register_scientific(self, candidate, *, run_id, session_id, content_bytes=None):
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


class _Backend:
    name = "fake"

    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []

    def search(self, query, *, max_results):
        self.calls.append((query, max_results))
        if self.failure:
            raise self.failure
        return WebSearchResult(
            provider=self.name, query=query,
            results=[WebSearchItem("A", "https://example.test/a", "snippet")],
        )


def test_web_search_freezes_bounded_receipt(tmp_path):
    register = _Register(tmp_path)
    backend = _Backend()
    observation = WebSearchTool(backend, register).execute(
        _state(), WebSearchInput(query="q", max_results=2),
    )

    assert observation.ok
    assert backend.calls == [("q", 2)]
    ref = next(iter(register.refs.values()))
    assert ref.kind == "web_search"
    path = register.registry.root / "run_example" / ref.id / "web_search.json"
    receipt = json.loads(path.read_text())
    assert receipt["results"][0]["url"] == "https://example.test/a"
    assert observation.memory_updates["web_output_artifact_ids"] == [ref.id]


def test_web_search_failure_freezes_negative_receipt(tmp_path):
    register = _Register(tmp_path)
    observation = WebSearchTool(
        _Backend(WebSearchError("limited", error_type="rate_limited", retry_after=4)),
        register,
    ).execute(_state(), WebSearchInput(query="q"))

    assert not observation.ok
    assert observation.value["error_type"] == "rate_limited"
    ref = next(iter(register.refs.values()))
    assert ref.kind == "web_search"
    receipt = json.loads((register.registry.root / "run_example" / ref.id / "web_search.json").read_text())
    assert receipt["status"] == "failed"
    assert receipt["retry_after"] == 4


class _Fetcher:
    def __init__(self, page=None, error=None):
        self.page = page
        self.error = error

    def fetch(self, url):
        if self.error:
            raise self.error
        return self.page


def test_web_fetch_freezes_readable_page_and_hash(tmp_path):
    register = _Register(tmp_path)
    page = WebPage(
        source_url="https://example.test/page",
        final_url="https://example.test/page",
        title="Title",
        text="Body",
        content_type="text/html",
        parser="test",
        fetched_at="2026-10-06T00:00:00+00:00",
    )
    observation = WebFetchTool(_Fetcher(page=page), register).execute(
        _state(), WebFetchInput(url=page.source_url),
    )

    assert observation.ok
    ref = next(iter(register.refs.values()))
    assert ref.kind == "web_page"
    reader = RegisteredArtifactReader([ref], run_id="run_example")
    content = reader.read_text(ref.id)["content"]
    assert "Title" in content and "Body" in content
    assert hashlib.sha256(content.encode()).hexdigest() == ref.sha256


def test_web_fetch_failure_does_not_freeze_page(tmp_path):
    register = _Register(tmp_path)
    observation = WebFetchTool(
        _Fetcher(error=__import__("resagent2_components").WebFetchError("bad", error_type="parse_failed")),
        register,
    ).execute(_state(), WebFetchInput(url="https://example.test/page"))

    assert not observation.ok
    assert observation.value["error_type"] == "parse_failed"
    assert register.refs == {}
