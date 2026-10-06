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
    assert receipt["provider_query"] == "q"
    assert observation.value["result_count"] == 1
    assert observation.value["omitted_count"] == 0 and not observation.value["truncated"]
    assert "executed_query" not in receipt
    assert observation.memory_updates["web_output_artifact_ids"] == [ref.id]


def test_web_search_failure_freezes_negative_receipt(tmp_path):
    register = _Register(tmp_path)
    observation = WebSearchTool(
        _Backend(WebSearchError("limited", error_type="rate_limited", retry_after=4)),
        register,
    ).execute(_state(), WebSearchInput(query="q"))

    assert not observation.ok
    assert observation.value["error_type"] == "rate_limited"
    assert observation.value["result_count"] == observation.value["omitted_count"] == 0
    assert observation.value["truncated"] is False
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


def test_later_deepseek_batches_remain_readable_after_preview(tmp_path):
    from resagent2_components import DeepSeekWebSearchBackend

    class HostedClient:
        def __init__(self):
            self.calls = []

        def set_trace_context(self, **kwargs):
            self.trace_context = kwargs

        def request(self, body):
            self.calls.append(body)
            return {"stop_reason": "end_turn", "content": [
                {"type": "web_search_tool_result", "content": [
                    {"type": "web_search_result", "title": f"Source {i}",
                     "url": f"https://example.test/{i}", "encrypted_content": "opaque"}
                    for i in range(start, start + 8)
                ]} for start in (0, 8, 16)
            ]}

    register, client = _Register(tmp_path), HostedClient()
    observation = WebSearchTool(DeepSeekWebSearchBackend(client), register).execute(
        _state(), WebSearchInput(query="find sources", max_results=5),
    )
    assert observation.ok and len(client.calls) == 1
    assert len(observation.value["results"]) == 5
    assert observation.value["result_count"] == 24
    assert observation.value["omitted_count"] == 19
    assert observation.value["truncated"] is True
    assert "19 more available with read_artifact" in observation.summary
    ref = next(iter(register.refs.values()))
    content = RegisteredArtifactReader([ref], run_id="run_example").read_text(ref.id)["content"]
    receipt = json.loads(content)
    assert [item["url"] for item in receipt["results"]] == [
        f"https://example.test/{i}" for i in range(24)
    ]
    assert all(item["snippet"] == "" for item in receipt["results"])
    assert "opaque" not in content
    assert hashlib.sha256(content.encode()).hexdigest() == ref.sha256


def test_successful_empty_search_has_no_omitted_results(tmp_path):
    backend = _Backend()
    backend.search = lambda query, **kwargs: WebSearchResult(provider="fake", query=query)
    register = _Register(tmp_path)
    observation = WebSearchTool(backend, register).execute(_state(), WebSearchInput(query="q"))
    assert observation.ok and observation.value["status"] == "empty"
    assert observation.value["results"] == []
    assert observation.value["result_count"] == observation.value["omitted_count"] == 0
    assert observation.value["truncated"] is False
