"""Generic one-shot model dispatch keeps shared accounting and private traces."""

import asyncio
import hashlib
import json
import stat
import traceback

import httpx
import pytest

from resagent2_runtime import ModelRequestClient, ModelRequestError, ModelRequestHTTPError
from resagent2_runtime import http, model_request
from resagent2_runtime.budget import (
    BudgetExhaustedError, DeadlineExceededError, MemoryUsage, current_budget,
    execution_budget, invoke_model,
)


KEY = "TEST_MODEL_SECRET_DO_NOT_LOG"
BODY = {"model": "generic-model", "input": "PRIVATE_REQUEST"}
PAYLOAD = {"output": "PRIVATE_RESPONSE", "usage": {"input_tokens": 7}, "stop_reason": "stop"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_MODEL_KEY", KEY)
    return ModelRequestClient(
        endpoint="https://model.example.test/request", api_key_env="TEST_MODEL_KEY",
        trace_dir=tmp_path / "trace", trace_level="full",
    )


def transport(monkeypatch, handler):
    """Exercise real shared HTTP transport against a deterministic in-memory peer."""
    original = httpx.AsyncClient
    monkeypatch.setattr(http.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs,
    ))


def records(client):
    return [json.loads(line) for line in
            (client.trace_dir / "llm_traces.jsonl").read_text().splitlines()]


def test_dispatch_is_precharged_once_and_shares_the_run_wallet(client, monkeypatch):
    usage = MemoryUsage()
    calls = []
    client.api_key_headers = ("Authorization", "x-api-key")
    client.extra_headers = {"provider-version": "v1", "authorization": "old-key"}

    def respond(request):
        calls.append(request)
        assert usage.used == 1
        assert list(usage.requests.values()) == ["unknown"]
        assert current_budget().usage is usage
        assert current_budget().remaining_calls == 0
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        assert request.headers["x-api-key"] == KEY
        assert request.headers["provider-version"] == "v1"
        assert request.method == "POST"
        assert json.loads(request.content) == BODY
        return httpx.Response(200, json=PAYLOAD)

    transport(monkeypatch, respond)
    with execution_budget(max_llm_calls=1, timeout_seconds=30, usage=usage):
        assert invoke_model(client, "request", BODY) == PAYLOAD
        with pytest.raises(BudgetExhaustedError):
            client.request(BODY)
    assert len(calls) == usage.used == 1
    assert list(usage.requests.values()) == ["succeeded"]
    assert client.last_attempts == 0
    assert len(records(client)) == 1


def test_standalone_requests_have_one_scoped_allowance(client, monkeypatch):
    wallets = []

    def respond(request):
        budget = current_budget()
        assert budget is not None
        assert budget.usage.used == 1
        assert budget.remaining_calls == 0
        wallets.append(budget.usage)
        return httpx.Response(200, json={})

    transport(monkeypatch, respond)
    assert client.request({}) == client.request({}) == {}
    assert current_budget() is None
    assert wallets[0] is not wallets[1]
    assert all(list(wallet.requests.values()) == ["succeeded"] for wallet in wallets)
    assert records(client)[0]["call_id"] != records(client)[1]["call_id"]


@pytest.mark.parametrize("deadline", [False, True])
def test_global_limit_denial_starts_no_http(client, monkeypatch, deadline):
    transport(monkeypatch, lambda request: pytest.fail("denied request was dispatched"))
    with execution_budget(max_llm_calls=1 if deadline else 0, timeout_seconds=0 if deadline else 30) as budget:
        with pytest.raises(DeadlineExceededError if deadline else BudgetExhaustedError):
            client.request(BODY)
        assert budget.usage.used == client.last_attempts == 0
    assert not client.trace_dir.exists()


def test_missing_key_is_resolved_at_dispatch_and_spends_nothing(client, monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(200, json={}))
    assert client.request({}) == {}
    monkeypatch.delenv("TEST_MODEL_KEY")
    with execution_budget(max_llm_calls=1, timeout_seconds=30) as budget:
        with pytest.raises(ModelRequestError, match="missing model API key") as raised:
            client.request(BODY)
        assert raised.value.error_type == "missing_credentials"
        assert budget.usage.used == client.last_attempts == 0
    assert len(records(client)) == 1


def test_failed_durable_charge_never_dispatches(client, monkeypatch):
    class UnavailableUsage(MemoryUsage):
        def charge(self, call_id, retry_index):
            raise OSError("cannot persist model charge")

    transport(monkeypatch, lambda request: pytest.fail("unpersisted request dispatched"))
    with execution_budget(max_llm_calls=1, timeout_seconds=30, usage=UnavailableUsage()):
        with pytest.raises(OSError, match="cannot persist"):
            client.request(BODY)
    assert client.last_attempts == 0
    assert not client.trace_dir.exists()


@pytest.mark.parametrize("failure, outcome, error_type", [
    ("http429", "failed", "http_error"),
    ("network", "unknown", "network_error"),
    ("timeout", "unknown", "timeout"),
    ("json", "failed", "invalid_response"),
    ("list", "failed", "invalid_response"),
    ("utf8", "failed", "invalid_response"),
    ("large", "failed", "response_too_large"),
])
def test_failed_attempt_is_charged_traced_and_never_retried(client, monkeypatch, failure, outcome, error_type):
    calls = []

    async def respond(request):
        calls.append(request)
        if failure == "http429":
            return httpx.Response(429, text=f"PRIVATE_PROVIDER_ERROR {KEY}", headers={"Retry-After": "17"})
        if failure == "network":
            raise httpx.ConnectError(f"PRIVATE_PROVIDER_ERROR {KEY}", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout(f"PRIVATE_PROVIDER_ERROR {KEY}", request=request)
        content = {"json": b"PRIVATE_INVALID_JSON", "list": b"[]", "utf8": b"\xff", "large": b"x" * 100}
        return httpx.Response(200, content=content[failure])

    client.max_response_bytes = 50
    transport(monkeypatch, respond)
    with execution_budget(max_llm_calls=9, timeout_seconds=30) as budget:
        with pytest.raises(ModelRequestError) as raised:
            client.request(BODY)
        assert raised.value.error_type == error_type
        assert list(budget.usage.requests.values()) == [outcome]
    assert client.last_attempts == len(calls) == budget.usage.used == 1
    public_error = "".join(traceback.format_exception(raised.value))
    assert KEY not in public_error
    assert "PRIVATE_PROVIDER_ERROR" not in public_error
    if failure == "http429":
        assert isinstance(raised.value, ModelRequestHTTPError)
        assert raised.value.status_code == 429
        assert raised.value.retry_after == "17"
        assert not hasattr(raised.value, "response")
        assert not hasattr(raised.value, "request")
    record = records(client)[0]
    assert record["outcome"] == outcome
    assert record["response_valid"] is False
    assert record["error_type"] == error_type
    assert record["validation_error"]
    assert record["retry_number"] == 0
    assert KEY not in json.dumps(record)
    assert "PRIVATE_PROVIDER_ERROR" not in json.dumps(record)


@pytest.mark.parametrize("run_limited", [False, True])
def test_http_timeout_is_clipped_to_run_and_local_time(client, monkeypatch, run_limited):
    calls = []

    async def respond(request):
        calls.append(request)
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={})

    transport(monkeypatch, respond)
    client.timeout_seconds = 20 if run_limited else 0.01
    with execution_budget(max_llm_calls=2, timeout_seconds=0.01 if run_limited else 30) as budget:
        with pytest.raises(DeadlineExceededError if run_limited else ModelRequestError) as raised:
            client.request(BODY)
        if not run_limited:
            assert raised.value.error_type == "timeout"
        assert list(budget.usage.requests.values()) == ["unknown"]
    assert len(calls) == 1
    assert records(client)[0]["outcome"] == "unknown"


@pytest.mark.parametrize("trace_level", ["off", "metadata", "full"])
def test_trace_levels_preserve_correlation_without_credentials(client, monkeypatch, trace_level):
    client.trace_level = trace_level
    client.set_trace_context(run_id="run-1", session_id="session-1", agent_name="scientific", tool="web_search", operation="web_search")
    payload = {**PAYLOAD, "echo_key": KEY, "usage": {"input_tokens": 7, "body": "PRIVATE_PROVIDER_METADATA"}}
    transport(monkeypatch, lambda request: httpx.Response(200, json=payload))
    assert client.request(BODY) == payload
    if trace_level == "off":
        assert not client.trace_dir.exists()
        return
    record = records(client)[0]
    assert record["run_id"] == "run-1"
    assert record["session_id"] == "session-1"
    assert record["tool"] == record["operation"] == "web_search"
    assert record["model"] == "generic-model"
    assert record["protocol"] == "json-model-request/v1"
    assert record["usage"] == {"input_tokens": 7}
    assert record["stop_reason"] == "stop"
    assert record["response_valid"] is True
    assert record["call_id"] and record["created_at"] and record["latency_ms"] >= 0
    assert stat.S_IMODE(client.trace_dir.stat().st_mode) == 0o700
    path = client.trace_dir / "llm_traces.jsonl"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    content = path.read_text()
    assert KEY not in content
    assert "Authorization" not in content
    if trace_level == "metadata":
        assert "PRIVATE_PROVIDER_METADATA" not in content
        assert "PRIVATE_" not in content
        assert "request_text" not in record and "raw_response_text" not in record
        assert record["request_sha256"] == hashlib.sha256(json.dumps(BODY, ensure_ascii=False).encode()).hexdigest()
        assert record["response_sha256"]
    else:
        assert json.loads(record["request_text"]) == BODY
        assert json.loads(record["raw_response_text"])["echo_key"] == "[REDACTED]"
        assert json.loads(record["raw_response_text"])["output"] == "PRIVATE_RESPONSE"


@pytest.mark.parametrize("changes", [
    {"endpoint": "file:///tmp/model"}, {"endpoint": "https://user:secret@model.test/request"},
    {"api_key_env": ""}, {"timeout_seconds": 0}, {"timeout_seconds": float("nan")},
    {"max_response_bytes": 0}, {"max_response_bytes": 1.5}, {"api_key_headers": ()},
    {"trace_level": "verbose"}, {"trace_level": "full"},
])
def test_constructor_rejects_invalid_configuration(changes):
    options = {"endpoint": "https://model.test/request", "api_key_env": "TEST_MODEL_KEY", **changes}
    with pytest.raises(ValueError):
        ModelRequestClient(**options)


def test_full_trace_redacts_credentials_from_request_and_context(client, monkeypatch):
    client.set_trace_context(operation="request", correlation=f"echo {KEY}")
    transport(monkeypatch, lambda request: httpx.Response(200, json={}))
    client.request({"model": "generic", "private_value": KEY})
    record = records(client)[0]
    assert KEY not in json.dumps(record)
    assert json.loads(record["request_text"])["private_value"] == "[REDACTED]"
    assert record["correlation"] == "echo [REDACTED]"


def test_retry_after_does_not_retain_arbitrary_provider_text(client, monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(
        429, headers={"Retry-After": f"Fri, 31 Dec 1999 23:59:59 GMT {KEY}"},
    ))
    with pytest.raises(ModelRequestHTTPError) as raised:
        client.request({})
    assert raised.value.retry_after is None
    assert KEY not in json.dumps(records(client))


def test_slow_charge_cannot_dispatch_after_run_deadline(client, monkeypatch):
    ticks = [0.0]

    class SlowUsage(MemoryUsage):
        def charge(self, call_id, retry_index):
            super().charge(call_id, retry_index)
            ticks[0] = 2.0

    usage = SlowUsage()
    transport(monkeypatch, lambda request: pytest.fail("late request dispatched"))
    with execution_budget(max_llm_calls=2, timeout_seconds=1, usage=usage, clock=lambda: ticks[0]):
        with pytest.raises(DeadlineExceededError):
            client.request({})
    assert client.last_attempts == usage.used == 1
    assert list(usage.requests.values()) == ["unknown"]
    assert records(client)[0]["error_type"] == "deadline_exceeded"


def test_valid_http_date_retry_after_remains_available(client, monkeypatch):
    date = "Fri, 31 Dec 1999 23:59:59 GMT"
    transport(monkeypatch, lambda request: httpx.Response(429, headers={"Retry-After": date}))
    with pytest.raises(ModelRequestHTTPError) as raised:
        client.request({})
    assert raised.value.retry_after == date
