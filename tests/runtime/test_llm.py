"""Bounded transient-failure retry for the OpenAI-compatible LLM client."""

import json
from unittest import mock
from urllib.error import URLError

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


def test_transient_failure_exhausts_retries(monkeypatch) -> None:
    client = _client(monkeypatch)
    with (
        mock.patch("resagent2_runtime.llm.time.sleep"),
        mock.patch("resagent2_runtime.llm.urlopen", side_effect=URLError("boom")),
    ):
        with pytest.raises(RuntimeError, match="3 attempts"):
            client.next_action(_context(), AgentAction)


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
