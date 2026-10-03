"""Bounded HTTP bodies retain the shared deadline and default transport behavior."""

import asyncio
import gzip

import httpx
import pytest

from resagent2_runtime import http
from resagent2_runtime.budget import DeadlineExceededError, execution_budget


class Body(httpx.AsyncByteStream):
    def __init__(self, chunks, *, delay=0):
        self.chunks = chunks
        self.delay = delay
        self.read_count = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.read_count += 1
            yield chunk

    async def aclose(self):
        self.closed = True


def transport(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(
        http.httpx, "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_bounded_body_matches_default_response(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(
        200, stream=Body([b"a", b"bc"]), headers={"x-source": "test"},
    ))
    request = httpx.Request("GET", "https://example.test/paper.pdf")
    bounded = http.send_request(request, timeout=1, max_response_bytes=3)
    default = http.send_request(request, timeout=1)
    assert bounded.content == default.content == b"abc"
    assert bounded.headers["x-source"] == "test"
    assert bounded.request.url == request.url


def test_byte_limit_stops_reading_and_closes_response(monkeypatch):
    body = Body([b"a" * 65536] * 4)
    transport(monkeypatch, lambda request: httpx.Response(200, stream=body))
    with pytest.raises(http.ResponseTooLargeError, match="65536 decoded bytes"):
        http.send_request(
            httpx.Request("GET", "https://example.test/large"),
            timeout=1, max_response_bytes=65536,
        )
    assert body.read_count == 2
    assert body.closed


def test_limit_counts_decoded_gzip_bytes(monkeypatch):
    content = b"x" * 100000
    compressed = gzip.compress(content)
    assert len(compressed) < 1000
    transport(monkeypatch, lambda request: httpx.Response(
        200, stream=Body([compressed]),
        headers={"content-encoding": "gzip", "content-length": str(len(compressed))},
    ))
    request = httpx.Request("GET", "https://example.test/compressed")
    with pytest.raises(http.ResponseTooLargeError, match="1000 decoded bytes"):
        http.send_request(request, timeout=1, max_response_bytes=1000)
    response = http.send_request(request, timeout=1, max_response_bytes=len(content))
    assert response.content == content
    assert response.headers["content-length"] == str(len(content))
    assert "content-encoding" not in response.headers


def test_redirects_are_opt_in_and_bounded(monkeypatch):
    calls = []

    def redirect(request):
        calls.append(request.url)
        return httpx.Response(302, headers={"location": "/again"}, stream=Body([]))

    transport(monkeypatch, redirect)
    request = httpx.Request("GET", "https://example.test/start")
    with pytest.raises(httpx.HTTPStatusError):
        http.send_request(request, timeout=1, max_response_bytes=10)
    assert len(calls) == 1
    calls.clear()
    with pytest.raises(httpx.TooManyRedirects):
        http.send_request(request, timeout=1, max_response_bytes=10, follow_redirects=True)
    assert len(calls) == 6


def test_bounded_body_honors_total_timeout_and_closes(monkeypatch):
    body = Body([b"x"], delay=0.1)
    transport(monkeypatch, lambda request: httpx.Response(200, stream=body))
    with pytest.raises(TimeoutError):
        http.send_request(
            httpx.Request("GET", "https://example.test/slow"),
            timeout=0.01, max_response_bytes=10,
        )
    assert body.closed


def test_redirect_body_is_discarded_without_unbounded_read(monkeypatch):
    redirect_body = Body([b"x" * 100000], delay=10)

    def respond(request):
        if request.url.path == "/first":
            return httpx.Response(302, headers={"location": "/paper"}, stream=redirect_body)
        return httpx.Response(200, stream=Body([b"pdf"]))

    transport(monkeypatch, respond)
    response = http.send_request(
        httpx.Request("GET", "https://example.test/first"),
        timeout=1, max_response_bytes=3, follow_redirects=True,
    )
    assert response.content == b"pdf"
    assert response.url.path == "/paper"
    assert len(response.history) == 1
    assert redirect_body.closed
    assert redirect_body.read_count == 0


def test_bounded_body_cannot_start_after_run_deadline(monkeypatch):
    transport(monkeypatch, lambda request: pytest.fail("HTTP should not start"))
    with execution_budget(max_llm_calls=1, timeout_seconds=0):
        with pytest.raises(DeadlineExceededError):
            http.send_request(
                httpx.Request("GET", "https://example.test/late"),
                timeout=100, max_response_bytes=10,
            )


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_byte_limit_does_not_start_http(monkeypatch, limit):
    transport(monkeypatch, lambda request: pytest.fail("HTTP should not start"))
    with pytest.raises(ValueError, match="must be positive"):
        http.send_request(
            httpx.Request("GET", "https://example.test/"),
            timeout=1, max_response_bytes=limit,
        )
