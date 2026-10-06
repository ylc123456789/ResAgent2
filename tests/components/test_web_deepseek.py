"""Hosted search keeps native source identity and exposes failures honestly."""

import pytest

from resagent2_components import DeepSeekWebSearchBackend, WebSearchError
from resagent2_runtime.budget import BudgetExhaustedError, DeadlineExceededError
from resagent2_runtime.model_request import ModelRequestError, ModelRequestHTTPError


URL = "https://example.test/source"


class Client:
    def __init__(self, content=None, *, error=None, stop_reason="end_turn"):
        self.payload = {"content": content, "stop_reason": stop_reason}
        self.error = error
        self.requests = []
        self.trace_context = {}

    def request(self, body):
        self.requests.append(body)
        if self.error:
            raise self.error
        return self.payload

    def set_trace_context(self, **kwargs):
        self.trace_context = kwargs


def result_block(*items):
    return {"type": "web_search_tool_result", "content": list(items)}


def source(url=URL, **kwargs):
    return {"type": "web_search_result", "url": url, "title": "Source", **kwargs}


def test_search_uses_isolated_query_and_url_matched_citations():
    client = Client([
        result_block(source(page_age="2026-10-01"), source()),
        {"type": "text", "text": "Do not use this generated summary as a snippet", "citations": [
            {"url": URL, "cited_text": "x" * 2400},
            {"url": URL, "cited_text": "duplicate excerpt"},
        ]},
        result_block(source("https://example.test/uncited")),
    ])
    backend = DeepSeekWebSearchBackend(client)
    result = backend.search("  a query  ", max_results=2)

    assert result.provider == "deepseek" and result.query == "a query"
    assert len(result.results) == 2
    assert result.results[0].snippet == "x" * 2000
    assert result.results[0].published_at == "2026-10-01"
    assert result.results[1].snippet == ""
    body, = client.requests
    assert len(body["messages"]) == 1
    message = body["messages"][0]
    assert message["role"] == "user" and len(message["content"]) == 1
    text = message["content"][0]["text"]
    assert "only for source discovery" in text
    assert "do not answer the underlying question" in text
    assert text.endswith("Query:\na query")
    assert body["model"] == "deepseek-flash"
    assert body["max_tokens"] == 4096
    assert body["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]


def test_preview_limit_does_not_discard_later_native_results():
    client = Client([
        result_block(source("https://example.test/a", title="")),
        result_block(source("https://example.test/a"), source("https://example.test/b")),
    ])
    result = DeepSeekWebSearchBackend(client).search("q", max_results=1)
    assert [item.url for item in result.results] == [
        "https://example.test/a", "https://example.test/b",
    ]
    assert result.results[0].title == result.results[0].url
    assert len(client.requests) == 1


def test_explicit_native_empty_result_is_successful_empty():
    result = DeepSeekWebSearchBackend(Client([result_block()])).search("q", max_results=1)
    assert result.results == []


def test_prose_urls_without_native_results_are_not_search_evidence():
    with pytest.raises(WebSearchError) as raised:
        DeepSeekWebSearchBackend(Client([{"type": "text", "text": URL}])).search("q", max_results=1)
    assert raised.value.error_type == "search_not_executed"


@pytest.mark.parametrize("stop_reason", ["max_tokens", "pause_turn", None])
def test_incomplete_model_response_is_failure(stop_reason):
    with pytest.raises(WebSearchError) as raised:
        DeepSeekWebSearchBackend(Client([result_block(source())], stop_reason=stop_reason)).search("q", max_results=1)
    assert raised.value.error_type == "incomplete_response"


@pytest.mark.parametrize("content", [
    None, {}, [None], [{"type": "web_search_tool_result", "content": None}],
    [result_block(None)], [result_block({})], [result_block(source(url="javascript:alert(1)"))],
    [result_block(source(url="https://user:pass@example.test/private"))],
    [result_block(source(url=None))],
    [result_block(), {"type": "text", "citations": {}}],
])
def test_malformed_results_are_not_successful_empty(content):
    with pytest.raises(WebSearchError) as raised:
        DeepSeekWebSearchBackend(Client(content)).search("q", max_results=1)
    assert raised.value.error_type == "invalid_response"


@pytest.mark.parametrize("code,expected", [("too_many_requests", "rate_limited"), ("unavailable", "provider_error")])
def test_native_tool_error_is_not_empty(code, expected):
    client = Client([{"type": "web_search_tool_result", "content": {
        "type": "web_search_tool_result_error", "error_code": code,
    }}])
    with pytest.raises(WebSearchError) as raised:
        DeepSeekWebSearchBackend(client).search("q", max_results=1)
    assert raised.value.error_type == expected


def test_http_limit_preserves_retry_after():
    with pytest.raises(WebSearchError) as raised:
        DeepSeekWebSearchBackend(Client(error=ModelRequestHTTPError(429, "4"))).search("q", max_results=1)
    assert raised.value.error_type == "rate_limited"
    assert raised.value.retry_after == 4


@pytest.mark.parametrize("error_type", ["network_error", "timeout", "invalid_response", "response_too_large", "missing_credentials"])
def test_transport_error_preserves_classification(error_type):
    with pytest.raises(WebSearchError) as raised:
        DeepSeekWebSearchBackend(Client(error=ModelRequestError("failure", error_type=error_type))).search("q", max_results=1)
    assert raised.value.error_type == error_type


@pytest.mark.parametrize("error", [BudgetExhaustedError("exhausted"), DeadlineExceededError("expired")])
def test_run_limits_propagate_to_runtime(error):
    with pytest.raises(type(error)):
        DeepSeekWebSearchBackend(Client(error=error)).search("q", max_results=1)


@pytest.mark.parametrize("query,count", [("", 1), ("q", 0), ("q", 11), ("q", True)])
def test_invalid_query_does_not_dispatch(query, count):
    client = Client([])
    with pytest.raises(WebSearchError):
        DeepSeekWebSearchBackend(client).search(query, max_results=count)
    assert client.requests == []
