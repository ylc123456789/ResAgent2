"""Bounded transient-failure retry for the OpenAI-compatible LLM client."""

import json
from io import BytesIO
from unittest import mock
from urllib.error import HTTPError, URLError

import pytest

from resagent2_runtime import ComposedContext, OpenAICompatibleClient
from resagent2_runtime.models import AgentAction


class _FakeResponse:
    """Context-manager stand-in for a urlopen response."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _client(monkeypatch) -> OpenAICompatibleClient:
    monkeypatch.setenv("TEST_LLM_KEY", "dummy")
    return OpenAICompatibleClient(
        model="test-model",
        api_base="https://example.com/v1",
        api_key_env="TEST_LLM_KEY",
    )


def _context() -> ComposedContext:
    return ComposedContext(
        text="do something",
        included_sections=[],
        omitted_sections=[],
        estimated_tokens=0,
    )


def test_transient_llm_failure_is_retried(monkeypatch) -> None:
    client = _client(monkeypatch)
    ok = _FakeResponse(
        {"choices": [{"message": {"content": json.dumps({"tool": "finish"})}}]}
    )
    with (
        mock.patch("resagent2_runtime.llm.time.sleep"),
        mock.patch(
            "resagent2_runtime.llm.urlopen", side_effect=[URLError("boom"), ok]
        ) as urlopen_mock,
    ):
        result = client.next_action(_context(), AgentAction)

    assert result == {"tool": "finish"}
    assert urlopen_mock.call_count == 2


def test_trace_writes_jsonl_when_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TEST_LLM_KEY", "dummy")
    client = OpenAICompatibleClient(
        model="test-model",
        api_base="https://example.com/v1",
        api_key_env="TEST_LLM_KEY",
        trace_dir=tmp_path / "traces",
        trace_level="full",
    )
    client.set_trace_context(
        run_id="run_x", session_id="session_s", agent="test", step=1
    )
    ok = _FakeResponse(
        {"choices": [{"message": {"content": json.dumps({"tool": "finish"})}}]}
    )
    with mock.patch("resagent2_runtime.llm.urlopen", return_value=ok):
        result = client.next_action(_context(), AgentAction)

    assert result == {"tool": "finish"}
    trace_file = tmp_path / "traces" / "llm_traces.jsonl"
    records = [
        json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    record = records[0]
    assert record["run_id"] == "run_x"
    assert record["session_id"] == "session_s"
    assert record["agent"] == "test"
    assert record["step"] == 1
    assert record["model"] == "test-model"
    assert record["request_text"]
    assert record["raw_response_text"] == json.dumps({"tool": "finish"})
    assert record["parsed_action"] == {"tool": "finish"}


def test_trace_metadata_level_omits_full_text(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TEST_LLM_KEY", "dummy")
    client = OpenAICompatibleClient(
        model="test-model",
        api_base="https://example.com/v1",
        api_key_env="TEST_LLM_KEY",
        trace_dir=tmp_path / "traces",
        trace_level="metadata",
    )
    ok = _FakeResponse(
        {"choices": [{"message": {"content": json.dumps({"tool": "finish"})}}]}
    )
    with mock.patch("resagent2_runtime.llm.urlopen", return_value=ok):
        client.next_action(_context(), AgentAction)

    trace_file = tmp_path / "traces" / "llm_traces.jsonl"
    record = json.loads(trace_file.read_text(encoding="utf-8").splitlines()[0])
    assert "request_text" not in record
    assert "raw_response_text" not in record
    assert "parsed_action" not in record
    assert record["request_sha256"]
    assert record["response_sha256"]
    assert record["action_sha256"]
    assert record["tool"] == "finish"
    assert record["action_valid"] is True


def test_trace_preserves_bad_json_response(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TEST_LLM_KEY", "dummy")
    client = OpenAICompatibleClient(
        model="test-model",
        api_base="https://example.com/v1",
        api_key_env="TEST_LLM_KEY",
        trace_dir=tmp_path / "traces",
        trace_level="full",
    )
    bad = _FakeResponse(
        {"choices": [{"message": {"content": "not valid json"}}]}
    )
    with (
        mock.patch("resagent2_runtime.llm.time.sleep"),
        mock.patch("resagent2_runtime.llm.urlopen", return_value=bad),
    ):
        with pytest.raises(RuntimeError, match="3 attempts"):
            client.next_action(_context(), AgentAction)

    trace_file = tmp_path / "traces" / "llm_traces.jsonl"
    record = json.loads(trace_file.read_text(encoding="utf-8").splitlines()[-1])
    assert record["action_valid"] is False
    assert record["raw_response_text"] == "not valid json"
    assert record["validation_error"]


def test_transient_failure_exhausts_retries(monkeypatch) -> None:
    client = _client(monkeypatch)
    with (
        mock.patch("resagent2_runtime.llm.time.sleep"),
        mock.patch("resagent2_runtime.llm.urlopen", side_effect=URLError("boom")),
    ):
        with pytest.raises(RuntimeError, match="3 attempts"):
            client.next_action(_context(), AgentAction)


def test_attempt_limit_caps_transient_retries(monkeypatch) -> None:
    client = _client(monkeypatch)
    client.set_attempt_limit(1)
    with mock.patch(
        "resagent2_runtime.llm.urlopen", side_effect=URLError("boom")
    ) as urlopen_mock:
        with pytest.raises(RuntimeError, match="after 1 attempts"):
            client.next_action(_context(), AgentAction)
    assert urlopen_mock.call_count == 1
    assert client.last_attempts == 1


def test_client_error_counts_one_attempt_after_prior_retry(monkeypatch) -> None:
    client = _client(monkeypatch)
    ok = _FakeResponse(
        {"choices": [{"message": {"content": json.dumps({"tool": "finish"})}}]}
    )
    with (
        mock.patch("resagent2_runtime.llm.time.sleep"),
        mock.patch("resagent2_runtime.llm.urlopen", side_effect=[URLError("boom"), ok]),
    ):
        client.next_action(_context(), AgentAction)
    assert client.last_attempts == 2

    error = HTTPError(
        url="https://example.com",
        code=400,
        msg="bad request",
        hdrs=None,
        fp=BytesIO(b"invalid"),
    )
    with mock.patch("resagent2_runtime.llm.urlopen", side_effect=error):
        with pytest.raises(RuntimeError, match="LLM HTTP 400"):
            client.next_action(_context(), AgentAction)
    assert client.last_attempts == 1


def test_malformed_json_is_retried(monkeypatch) -> None:
    client = _client(monkeypatch)
    malformed = _FakeResponse(
        {"choices": [{"message": {"content": "not valid json"}}]}
    )
    ok = _FakeResponse(
        {"choices": [{"message": {"content": json.dumps({"tool": "finish"})}}]}
    )
    with (
        mock.patch("resagent2_runtime.llm.time.sleep"),
        mock.patch(
            "resagent2_runtime.llm.urlopen", side_effect=[malformed, ok]
        ) as urlopen_mock,
    ):
        result = client.next_action(_context(), AgentAction)

    assert result == {"tool": "finish"}
    assert urlopen_mock.call_count == 2
