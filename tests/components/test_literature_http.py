"""HTTP policy tests use virtual time, never live sleeps or requests."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
import httpx

import pytest

from resagent2_components.literature import _http as http
from resagent2_components.literature import backends as literature


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(http.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(http.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    return now


def http_error(status, retry_after=None):
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    response = httpx.Response(status, headers=headers, request=httpx.Request("GET", "https://example.test/?q=x"))
    return httpx.HTTPStatusError("private response", request=response.request, response=response)


def test_arxiv_instances_share_spacing(clock, monkeypatch):
    monkeypatch.setattr(literature, "_ARXIV_HTTP", http.LiteratureHTTP("arXiv", interval_seconds=3))
    times = []
    def request(self, url):
        times.append(clock[0])
        return b'<feed xmlns="http://www.w3.org/2005/Atom"/>'
    monkeypatch.setattr(literature.ArxivLiteratureBackend, "_request", request)
    for _ in range(3):
        assert literature.ArxivLiteratureBackend().search("x", max_results=1) == []
    assert times == [0, 3, 6]


@pytest.mark.parametrize("retry_after,cooldown", [(None, 60), ("120", 120), ("bad", 60), ("-1", 60)])
def test_429_is_not_retried_and_cooldown_skips_network(clock, retry_after, cooldown):
    policy = http.LiteratureHTTP("test", interval_seconds=3)
    calls = []
    def request():
        calls.append(clock[0])
        if len(calls) == 1:
            raise http_error(429, retry_after)
        return b"ok"
    with pytest.raises(http.LiteratureUnavailableError, match="429"):
        policy.fetch(request, max_attempts=3)
    assert calls == [0]
    assert clock[0] == 0  # No long blocking wait.
    clock[0] = cooldown - 1
    with pytest.raises(http.LiteratureUnavailableError, match="cooling down"):
        policy.fetch(request, max_attempts=3)
    assert calls == [0]
    clock[0] = cooldown
    assert policy.fetch(request, max_attempts=3) == b"ok"
    assert calls == [0, cooldown]


def test_retry_after_supports_http_date():
    future = datetime.now(UTC) + timedelta(seconds=180)
    assert 178 <= http._retry_after(format_datetime(future, usegmt=True)) <= 180
    assert http._retry_after("yesterday") == 0


def test_503_with_retry_after_defers_instead_of_waiting(clock):
    policy = http.LiteratureHTTP("test", interval_seconds=3)
    def request():
        raise http_error(503, "3600")
    with pytest.raises(http.LiteratureUnavailableError, match="Retry-After"):
        policy.fetch(request, max_attempts=3)
    assert clock[0] == 0
    assert policy._cooldown_until == 3600


@pytest.mark.parametrize("failure", [TimeoutError(), httpx.ConnectError("offline"), ConnectionResetError(), http_error(503)])
def test_transient_failures_have_finite_spaced_attempts(clock, failure):
    policy = http.LiteratureHTTP("test", interval_seconds=3)
    times = []
    def request():
        times.append(clock[0])
        raise failure
    with pytest.raises(http.LiteratureUnavailableError, match="after 3 attempts"):
        policy.fetch(request, max_attempts=3)
    assert times == [0, 3, 9]


def test_transient_failure_can_recover(clock):
    policy = http.LiteratureHTTP("test", interval_seconds=3)
    calls = []
    def request():
        calls.append(clock[0])
        if len(calls) < 2:
            raise TimeoutError()
        return b"ok"
    assert policy.fetch(request, max_attempts=3) == b"ok"
    assert calls == [0, 3]


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_request_errors_are_not_retried_or_classified_as_unavailable(clock, status):
    policy = http.LiteratureHTTP("test", interval_seconds=3)
    calls = []
    def request():
        calls.append(clock[0])
        raise http_error(status)
    with pytest.raises(http.LiteratureSearchError, match=f"HTTP {status}") as caught:
        policy.fetch(request, max_attempts=3)
    assert not isinstance(caught.value, http.LiteratureUnavailableError)
    assert "private" not in str(caught.value)
    assert calls == [0]


def test_programming_errors_are_not_swallowed(clock):
    def request():
        raise RuntimeError("bug")
    with pytest.raises(RuntimeError, match="bug"):
        http.LiteratureHTTP("test", interval_seconds=3).fetch(request, max_attempts=3)
