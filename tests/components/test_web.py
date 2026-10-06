"""Deterministic tests for provider-neutral web components."""

import json
import socket
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from resagent2_components import (
    WebFetchError,
    WebPageFetcher,
    WebSearchError,
    TavilyWebSearchBackend,
)
from resagent2_components.web import _retry_after
from resagent2_runtime.http import NonPublicAddressError, ResponseTooLargeError


def _response(content: bytes, *, status=200, content_type="application/json", url="https://api.test/search"):
    return httpx.Response(
        status,
        content=content,
        headers={"content-type": content_type},
        request=httpx.Request("POST", url),
    )


@pytest.fixture(autouse=True)
def resolve_test_hosts_as_public(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda host, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ],
    )


def test_tavily_normalizes_results_and_bounds_snippet(monkeypatch):
    observed = {}

    def send(request, **kwargs):
        observed["request"] = request
        observed["kwargs"] = kwargs
        return _response(json.dumps({
            "results": [{
                "title": "A",
                "url": "https://example.test/a",
                "content": "x" * 2500,
                "score": 0.9,
                "published_date": "2026-01-01",
            }],
        }).encode())

    monkeypatch.setattr("resagent2_components.web.send_request", send)
    result = TavilyWebSearchBackend("secret", timeout_seconds=7).search(
        "  query  ", max_results=3,
    )

    assert result.provider == "tavily"
    assert result.query == "query"
    assert result.results[0].snippet == "x" * 2000
    assert observed["kwargs"]["timeout"] == 7
    assert observed["request"].headers["User-Agent"].startswith("ResAgent2/")
    payload = json.loads(observed["request"].content)
    assert observed["request"].headers["Authorization"] == "Bearer secret"
    assert "api_key" not in payload
    assert payload["query"] == "query"
    assert payload["include_raw_content"] is False


def test_tavily_keeps_received_results_even_if_provider_exceeds_hint(monkeypatch):
    requests = []

    def send(request, **kwargs):
        requests.append(json.loads(request.content))
        return _response(json.dumps({"results": [
            {"title": "A", "url": "https://example.test/a"},
            {"title": "B", "url": "https://example.test/b"},
        ]}).encode())

    monkeypatch.setattr("resagent2_components.web.send_request", send)
    result = TavilyWebSearchBackend("secret").search("q", max_results=1)
    assert requests[0]["max_results"] == 1
    assert [item.url for item in result.results] == [
        "https://example.test/a", "https://example.test/b",
    ]


@pytest.mark.parametrize("content", [b"[]", b"null", b'{"results": {}}', b"{bad"])
def test_tavily_invalid_json_is_structured_error(monkeypatch, content):
    monkeypatch.setattr(
        "resagent2_components.web.send_request",
        lambda request, **kwargs: _response(content),
    )

    with pytest.raises(WebSearchError, match="invalid JSON") as raised:
        TavilyWebSearchBackend("secret").search("q", max_results=1)
    assert raised.value.error_type == "invalid_response"


def test_tavily_rate_limit_preserves_retry_after(monkeypatch):
    retry_at = (datetime.now(UTC) + timedelta(seconds=60)).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )

    def send(request, **kwargs):
        response = _response(b'{"error":"slow down"}', status=429)
        response.headers["Retry-After"] = retry_at
        response.raise_for_status()
        return response

    monkeypatch.setattr("resagent2_components.web.send_request", send)
    with pytest.raises(WebSearchError) as raised:
        TavilyWebSearchBackend("secret").search("q", max_results=1)
    assert raised.value.error_type == "rate_limited"
    assert raised.value.retry_after is not None and raised.value.retry_after > 0


def test_tavily_response_limit_is_structured_error(monkeypatch):
    def send(request, **kwargs):
        raise ResponseTooLargeError("too large")

    monkeypatch.setattr("resagent2_components.web.send_request", send)
    with pytest.raises(WebSearchError, match="byte limit") as raised:
        TavilyWebSearchBackend("secret").search("q", max_results=1)
    assert raised.value.error_type == "response_too_large"


def test_retry_after_accepts_seconds_and_http_date():
    assert _retry_after("2") == 2.0
    assert _retry_after("not-a-date") == 0.0
    future = (datetime.now(UTC) + timedelta(seconds=30)).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )
    assert 0 < _retry_after(future) <= 30


def test_web_page_fetcher_extracts_html_and_ignores_noncontent(monkeypatch):
    html = b"""<html><head><title>  Page title </title><style>hide</style></head>
    <body><script>bad()</script><p>Hello <b>world</b>.</p>
    <noscript>also hidden</noscript><template>hidden</template></body></html>"""
    monkeypatch.setattr(
        "resagent2_components.web.send_request",
        lambda request, **kwargs: _response(
            html, content_type="text/html; charset=utf-8",
            url="https://example.test/page",
        ),
    )
    page = WebPageFetcher().fetch("https://example.test/page")

    assert page.title == "Page title"
    assert page.text == "Hello\nworld\n."
    assert page.parser == "html.parser"
    assert page.final_url == "https://example.test/page"


def test_web_page_fetcher_supports_plain_text(monkeypatch):
    monkeypatch.setattr(
        "resagent2_components.web.send_request",
        lambda request, **kwargs: _response(
            b"plain body", content_type="text/plain; charset=utf-8",
            url="https://example.test/text",
        ),
    )
    page = WebPageFetcher().fetch("https://example.test/text")
    assert page.title == ""
    assert page.text == "plain body"
    assert page.parser == "plain"


def test_web_page_fetcher_requires_utf8(monkeypatch):
    monkeypatch.setattr(
        "resagent2_components.web.send_request",
        lambda request, **kwargs: _response(
            b"\xff", content_type="text/plain; charset=latin-1",
            url="https://example.test/text",
        ),
    )
    with pytest.raises(WebFetchError, match="valid UTF-8") as raised:
        WebPageFetcher().fetch("https://example.test/text")
    assert raised.value.error_type == "invalid_encoding"


@pytest.mark.parametrize("content_type, content", [
    ("text/html", b"<title>Only title</title><script>render()</script>"),
    ("text/plain", b" \n\t"),
])
def test_web_page_fetcher_rejects_empty_extraction(monkeypatch, content_type, content):
    monkeypatch.setattr(
        "resagent2_components.web.send_request",
        lambda request, **kwargs: _response(
            content, content_type=content_type, url="https://example.test/page",
        ),
    )
    with pytest.raises(WebFetchError) as raised:
        WebPageFetcher().fetch("https://example.test/page")
    assert raised.value.error_type == "parse_failed"


@pytest.mark.parametrize("url", [
    "file:///tmp/secret",
    "ftp://example.test/file",
    "https://user:pass@example.test/private",
    "https://@example.test/private",
    "https://:pass@example.test/private",
    "http://[",
])
def test_web_page_fetcher_rejects_non_http_or_malformed_urls(url):
    with pytest.raises(WebFetchError) as raised:
        WebPageFetcher().fetch(url)
    assert raised.value.error_type == "invalid_url"


@pytest.mark.parametrize("url", [
    "http://localhost/private",
    "http://127.0.0.1/private",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/private",
])
def test_web_page_fetcher_rejects_nonpublic_hosts(url):
    with pytest.raises(WebFetchError) as raised:
        WebPageFetcher().fetch(url)
    assert raised.value.error_type == "invalid_url"


def test_web_page_fetcher_rejects_host_resolving_to_private_address(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 0)),
        ],
    )
    with pytest.raises(WebFetchError) as raised:
        WebPageFetcher().fetch("https://internal.example/page")
    assert raised.value.error_type == "invalid_url"


def test_web_page_fetcher_revalidates_redirect_targets(monkeypatch):
    calls = []

    def send(request, **kwargs):
        calls.append(request)
        assert kwargs["public_only"] is True
        if request.url.host == "127.0.0.1":
            raise NonPublicAddressError("private address")
        response = httpx.Response(
            302, headers={"location": "http://127.0.0.1/private"}, request=request,
        )
        response.raise_for_status()
        return response

    monkeypatch.setattr("resagent2_components.web.send_request", send)
    with pytest.raises(WebFetchError) as raised:
        WebPageFetcher().fetch("https://example.test/start")
    assert raised.value.error_type == "invalid_url"
    assert len(calls) == 2


def test_web_page_fetcher_follows_relative_redirect_and_preserves_final_url(monkeypatch):
    calls = []

    def send(request, **kwargs):
        calls.append((str(request.url), kwargs))
        if len(calls) == 1:
            response = httpx.Response(
                302, headers={"location": "/article"}, request=request,
            )
            response.raise_for_status()
        return httpx.Response(
            200, text="Article", headers={"content-type": "text/plain"}, request=request,
        )

    monkeypatch.setattr("resagent2_components.web.send_request", send)
    page = WebPageFetcher().fetch("https://example.test/start")
    assert page.final_url == "https://example.test/article"
    assert [url for url, _ in calls] == ["https://example.test/start", "https://example.test/article"]
    assert all(kwargs["public_only"] for _, kwargs in calls)
    assert calls[1][1]["timeout"] <= calls[0][1]["timeout"]


def test_web_page_fetcher_bounds_redirects(monkeypatch):
    calls = []

    def send(request, **kwargs):
        calls.append(request)
        response = httpx.Response(302, headers={"location": "/next"}, request=request)
        response.raise_for_status()

    monkeypatch.setattr("resagent2_components.web.send_request", send)
    with pytest.raises(WebFetchError) as raised:
        WebPageFetcher().fetch("https://example.test/start")
    assert raised.value.error_type == "redirect_limit"
    assert len(calls) == 6


def test_web_page_fetcher_normalizes_provider_invalid_url(monkeypatch):
    monkeypatch.setattr(
        "resagent2_components.web.send_request",
        lambda request, **kwargs: (_ for _ in ()).throw(httpx.InvalidURL("bad url")),
    )
    with pytest.raises(WebFetchError) as raised:
        WebPageFetcher().fetch("https://example.test/page")
    assert raised.value.error_type == "invalid_url"


@pytest.mark.parametrize("content_type, content, error_type", [
    ("application/pdf", b"%PDF-1.7", "unsupported_content_type"),
    ("text/plain", b"\x00binary", "invalid_encoding"),
])
def test_web_page_fetcher_rejects_unsupported_or_nontext(monkeypatch, content_type, content, error_type):
    monkeypatch.setattr(
        "resagent2_components.web.send_request",
        lambda request, **kwargs: _response(
            content, content_type=content_type, url="https://example.test/file",
        ),
    )
    with pytest.raises(WebFetchError) as raised:
        WebPageFetcher().fetch("https://example.test/file")
    assert raised.value.error_type == error_type
