"""Scientific web tools follow the existing runtime and artifact handoff."""

import hashlib
import json

import pytest

from resagent2_components import (
    RegisteredArtifactReader, WebFetchError, WebPage, WebSearchError,
    WebSearchItem, WebSearchResult,
)
from resagent2_contracts import (
    AgentPermissions, AgentRequest, ArtifactCandidate, ArtifactRef, TaskBudget,
)
from resagent2_orchestrator import ArtifactRegistry
from resagent2_orchestrator.interpreter import build_research_index
from resagent2_runtime import NativeToolCall, ToolCallTurn
from resagent2_scientific import ScientificAgent


RUN_ID = "run_web_integration"
URL = "https://example.test/source"


class Registration:
    def __init__(self, root):
        self.registry = ArtifactRegistry(root)
        self.refs = {}

    def register_scientific(self, candidate, **kwargs):
        ref = self.registry.register_scientific(candidate, **kwargs)
        self.refs[ref.id] = ref
        return ref

    def resolve(self, artifact_id, *, run_id):
        ref = self.refs.get(artifact_id)
        return ref if ref and ref.run_id == run_id else None

    def list_artifacts(self, *, run_id):
        return [ref for ref in self.refs.values() if ref.run_id == run_id]

    def of_kind(self, kind):
        return next(ref for ref in self.refs.values() if ref.kind == kind)


class SearchBackend:
    name = "test-search"

    def __init__(self, *, error=None, empty=False, results=None, incomplete_reason=None):
        self.error, self.empty, self.results = error, empty, results
        self.incomplete_reason = incomplete_reason
        self.calls = []

    def search(self, query, *, max_results):
        self.calls.append((query, max_results))
        if self.error:
            raise self.error
        return WebSearchResult(
            provider=self.name, query=query,
            results=[] if self.empty else (
                self.results if self.results is not None else [WebSearchItem("Source", URL, "A lead")]
            ), incomplete_reason=self.incomplete_reason,
        )


class PageFetcher:
    def __init__(self, *, error=None):
        self.error, self.calls = error, []

    def fetch(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        return WebPage(
            source_url=url, final_url=url, title="Source", text="Original page detail",
            content_type="text/html", parser="test", fetched_at="2026-10-06T00:00:00+00:00",
        )


class UnusedLiteratureBackend:
    def search(self, *args, **kwargs):
        pytest.fail("No literature step was requested by this action plan")


class NativePlanClient:
    tool_session_key = "scientific-web-test"

    def __init__(self, actions):
        self.actions = iter(actions)
        self.contexts, self.schemas, self.turns = [], [], []

    def next_tool_call(self, context, schemas, turns, **kwargs):
        self.contexts.append(context)
        self.schemas.append(schemas)
        self.turns.append(turns)
        action = next(self.actions)
        action = action() if callable(action) else action
        return ToolCallTurn(tool_calls=[NativeToolCall(
            id=f"call_{len(self.contexts)}", name=action["tool"],
            arguments=json.dumps(action["arguments"]),
        )])


def request(*, artifacts=(), budget=8):
    return AgentRequest(
        run_id=RUN_ID, agent="scientific", instruction="Answer using the needed sources",
        input_artifacts=list(artifacts), permissions=AgentPermissions(),
        budget=TaskBudget(max_llm_calls=budget, timeout_seconds=30),
    )


def finish(*refs):
    return {"tool": "finish", "arguments": {
        "report": "The available source material has been assessed",
        "artifacts": [{
            "kind": "scientific_opinion", "path": "opinion.json",
            "media_type": "application/json", "summary": "Judgment",
            "content": json.dumps({
                "verdict": "inconclusive", "statement": "Available source assessed",
                "evidence_artifact_ids": [ref.id for ref in refs],
                "limitations": ["No independent research measurements"],
            }),
        }],
    }}


def read(ref):
    return {"tool": "read_artifact", "arguments": {"artifact_id": ref.id}}


@pytest.mark.parametrize("search,fetch,registration", [
    (False, False, True), (True, False, True), (False, True, True),
    (True, True, True), (True, True, False),
])
def test_scientific_exposes_only_injected_web_tools(tmp_path, search, fetch, registration):
    client = NativePlanClient([finish()])
    result = ScientificAgent(
        client, web_search_backend=SearchBackend() if search else None,
        web_page_fetcher=PageFetcher() if fetch else None,
        registration_port=Registration(tmp_path) if registration else None,
    ).invoke(request())

    assert result.status == "completed", result.report
    names = {schema["function"]["name"] for schema in client.schemas[0]}
    assert ("web_search" in names) == (search and registration)
    assert ("web_fetch" in names) == (fetch and registration)
    assert {"run_shell", "run_verification", "create_file"}.isdisjoint(names)


def test_web_artifacts_are_live_readable_citable_and_returned_without_copying(tmp_path):
    register = Registration(tmp_path)
    backend, fetcher = SearchBackend(), PageFetcher()
    client = NativePlanClient([
        {"tool": "web_search", "arguments": {"query": "a source", "max_results": 2}},
        {"tool": "web_fetch", "arguments": {"url": URL}},
        lambda: read(register.of_kind("web_page")),
        lambda: finish(register.of_kind("web_search"), register.of_kind("web_page")),
    ])
    result = ScientificAgent(
        client, web_search_backend=backend, web_page_fetcher=fetcher,
        registration_port=register, literature_backend=UnusedLiteratureBackend(),
    ).invoke(request())

    assert result.status == "completed", result.report
    assert backend.calls == [("a source", 2)]
    assert fetcher.calls == [URL]
    refs = [ref for ref in result.artifacts if isinstance(ref, ArtifactRef)]
    assert {ref.id for ref in refs} == set(register.refs)
    assert len(refs) == 2
    index = build_research_index(run_id=RUN_ID, artifacts=refs, work_requests=[])
    assert {item.kind for group in index.groups for item in group.artifacts} == {"web_search", "web_page"}
    reader = RegisteredArtifactReader(refs, run_id=RUN_ID)
    page = reader.read_text(register.of_kind("web_page").id)["content"]
    assert "Original page detail" in page
    assert hashlib.sha256(page.encode()).hexdigest() == register.of_kind("web_page").sha256
    receipt = json.loads(reader.read_text(register.of_kind("web_search").id)["content"])
    assert receipt["provider"] == "test-search" and receipt["results"][0]["url"] == URL
    trace = next(item for item in result.artifacts if item.kind == "observation_trace")
    assert json.loads(trace.content)["observed_artifact_ids"] == [register.of_kind("web_page").id]
    assert "Original page detail" in json.dumps(client.turns[-1], default=lambda x: x.model_dump())


def test_web_fetch_can_be_used_without_search_or_literature_step(tmp_path):
    register, fetcher = Registration(tmp_path), PageFetcher()
    backend = SearchBackend()
    client = NativePlanClient([
        {"tool": "web_fetch", "arguments": {"url": URL}},
        lambda: read(register.of_kind("web_page")),
        lambda: finish(register.of_kind("web_page")),
    ])
    result = ScientificAgent(
        client, web_search_backend=backend, web_page_fetcher=fetcher,
        registration_port=register, literature_backend=UnusedLiteratureBackend(),
    ).invoke(request())

    assert result.status == "completed", result.report
    assert backend.calls == [] and fetcher.calls == [URL]
    assert {ref.kind for ref in register.refs.values()} == {"web_page"}


@pytest.mark.parametrize("failure", [False, True])
def test_empty_or_failed_search_receipt_cannot_supply_required_paper_evidence(tmp_path, failure):
    register = Registration(tmp_path / "artifacts")
    requirements = register.registry.register_system_artifact(
        ArtifactCandidate(
            kind="conclusion_requirements", path="requirements.json",
            media_type="application/json", summary="A paper is required",
            content=json.dumps({"required_evidence_kinds": ["literature_paper"]}),
        ), run_id=RUN_ID, source_type="conclusion_requirement",
    )
    backend = SearchBackend(
        error=WebSearchError("limited", error_type="rate_limited", retry_after=3) if failure else None,
        empty=True,
    )
    client = NativePlanClient([
        {"tool": "web_search", "arguments": {"query": "paper"}},
        lambda: finish(register.of_kind("web_search")),
        {"tool": "ask_user", "arguments": {
            "assessment": {"statement": "No required paper evidence is available"},
            "text": "The search supplied no paper. Please provide one or restore the service.",
            "requested_fields": ["paper"],
        }},
    ])
    result = ScientificAgent(
        client, web_search_backend=backend, registration_port=register,
    ).invoke(request(artifacts=[requirements]))

    assert result.status == "needs_user_input", result.report
    assert "literature_paper" in client.contexts[-1].text
    assert {ref.kind for ref in register.refs.values()} == {"web_search"}
    receipt = json.loads(RegisteredArtifactReader(
        list(register.refs.values()), run_id=RUN_ID,
    ).read_text(register.of_kind("web_search").id)["content"])
    assert receipt["status"] == ("failed" if failure else "empty")
    if failure:
        assert receipt["error_type"] == "rate_limited" and receipt["retry_after"] == 3
    assert register.of_kind("web_search") in result.artifacts


def test_failed_page_fetch_does_not_create_page_or_observation_record(tmp_path):
    register = Registration(tmp_path)
    client = NativePlanClient([
        {"tool": "web_fetch", "arguments": {"url": URL}}, finish(),
    ])
    result = ScientificAgent(
        client, web_page_fetcher=PageFetcher(error=WebFetchError(
            "unsupported PDF", error_type="unsupported_content_type",
        )), registration_port=register,
    ).invoke(request())

    assert result.status == "completed", result.report
    assert register.refs == {}
    trace = next(item for item in result.artifacts if item.kind == "observation_trace")
    assert json.loads(trace.content)["observed_artifact_ids"] == []
    assert "unsupported_content_type" in json.dumps(client.turns[-1], default=lambda x: x.model_dump())


@pytest.mark.parametrize("budget", [1, 2, 3])
def test_hosted_search_shares_scientific_budget_and_correlates_trace(tmp_path, monkeypatch, budget):
    import httpx
    from resagent2_components import DeepSeekWebSearchBackend
    from resagent2_runtime import ModelRequestClient

    monkeypatch.setenv("TEST_SEARCH_KEY", "test-only-secret")
    calls = []

    def send(wire_request, **kwargs):
        calls.append(wire_request)
        return httpx.Response(200, request=wire_request, json={
            "content": [{"type": "web_search_tool_result", "content": [
                {"type": "web_search_result", "title": "Source", "url": URL},
            ]}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 12, "output_tokens": 3},
        })

    monkeypatch.setattr("resagent2_runtime.model_request.send_request", send)
    register = Registration(tmp_path / "artifacts")
    hosted = ModelRequestClient(
        endpoint="https://api.test/messages", api_key_env="TEST_SEARCH_KEY",
        trace_dir=tmp_path / "trace", trace_level="full",
    )
    client = NativePlanClient([
        {"tool": "web_search", "arguments": {"query": "find source"}},
        lambda: finish(register.of_kind("web_search")),
    ])
    result = ScientificAgent(
        client, web_search_backend=DeepSeekWebSearchBackend(hosted), registration_port=register,
    ).invoke(request(budget=budget))

    assert result.llm_calls == budget
    if budget == 1:
        assert calls == [] and register.refs == {}
    else:
        assert len(calls) == 1
        trace, = [json.loads(line) for line in (tmp_path / "trace" / "llm_traces.jsonl").read_text().splitlines()]
        assert trace["run_id"] == RUN_ID
        assert trace["session_id"] == result.session.id
        assert trace["tool"] == "web_search"
        assert trace["usage"] == {"input_tokens": 12, "output_tokens": 3}
        assert trace["model"] == "deepseek-flash"
        assert "test-only-secret" not in json.dumps(trace)
        messages = json.loads(trace["request_text"])["messages"]
        assert len(messages) == 1 and messages[0]["role"] == "user"
        assert messages[0]["content"][0]["text"] == "Perform a web search for the query: find source"
        receipt = json.loads(RegisteredArtifactReader(
            list(register.refs.values()), run_id=RUN_ID,
        ).read_text(register.of_kind("web_search").id)["content"])
        assert receipt["provider"] == "deepseek"
        assert receipt["results"][0]["url"] == URL
    assert (result.status == "completed") == (budget == 3)


@pytest.mark.parametrize("incomplete_reason", [None, "max_uses_exceeded"])
def test_scientific_can_read_and_follow_a_source_omitted_from_search_preview(tmp_path, incomplete_reason):
    register, fetcher = Registration(tmp_path), PageFetcher()
    backend = SearchBackend(results=[
        WebSearchItem("First lead", "https://example.test/first", ""),
        WebSearchItem("Later source", URL, ""),
    ], incomplete_reason=incomplete_reason)
    client = NativePlanClient([
        {"tool": "web_search", "arguments": {"query": "sources", "max_results": 1}},
        lambda: read(register.of_kind("web_search")),
        {"tool": "web_fetch", "arguments": {"url": URL}},
        lambda: read(register.of_kind("web_page")),
        lambda: finish(register.of_kind("web_search"), register.of_kind("web_page")),
    ])
    result = ScientificAgent(
        client, web_search_backend=backend, web_page_fetcher=fetcher,
        registration_port=register, literature_backend=UnusedLiteratureBackend(),
    ).invoke(request())
    assert result.status == "completed", result.report
    first = json.loads(client.turns[1][0].tool_results["call_1"])["value"]
    assert first["result_count"] == 2 and first["omitted_count"] == 1
    assert first["truncated"] is True
    assert first["incomplete_reason"] == incomplete_reason
    assert first["status"] == ("partial" if incomplete_reason else "results")
    assert [item["url"] for item in first["results"]] == ["https://example.test/first"]
    assert URL in json.dumps(client.turns[2], default=lambda x: x.model_dump())
    assert backend.calls == [("sources", 1)] and fetcher.calls == [URL]
    refs = [ref for ref in result.artifacts if isinstance(ref, ArtifactRef)]
    index = build_research_index(run_id=RUN_ID, artifacts=refs, work_requests=[])
    assert {item.kind for group in index.groups for item in group.artifacts} == {"web_search", "web_page"}
