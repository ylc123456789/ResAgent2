"""CLI search selection is explicit while page fetching stays independent."""

import pytest

from resagent2_cli import composition
from resagent2_components import DeepSeekWebSearchBackend, TavilyWebSearchBackend, WebPageFetcher
from resagent2_runtime import ModelRequestClient


@pytest.fixture(autouse=True)
def configured_clients(monkeypatch):
    monkeypatch.setattr(composition, "_client", lambda: object())
    monkeypatch.setattr(composition, "_compiler_client", lambda **kwargs: object())
    for name in (
        "DEEPSEEK_API_KEY", "TAVILY_API_KEY", "RESAGENT2_WEB_SEARCH_PROVIDER",
        "RESAGENT2_WEB_SEARCH_MODEL", "RESAGENT2_WEB_SEARCH_TIMEOUT_SECONDS",
        "RESAGENT2_WEB_TIMEOUT_SECONDS", "RESAGENT2_WEB_MAX_RESPONSE_BYTES",
        "RESAGENT2_LLM_TRACE_LEVEL", "RESAGENT2_LLM_TRACE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


def scientific_application(tmp_path):
    return composition.build_application(data_root=tmp_path / "data").controller.scientific_port


@pytest.mark.parametrize("tavily_key", [None, "test-only-tavily-key"])
def test_default_search_without_deepseek_key_is_unavailable_without_fallback(
    tmp_path, monkeypatch, tavily_key,
):
    if tavily_key is not None:
        monkeypatch.setenv("TAVILY_API_KEY", tavily_key)
    scientific = scientific_application(tmp_path)

    assert scientific.web_search_backend is None
    assert isinstance(scientific.web_page_fetcher, WebPageFetcher)
    assert scientific.web_page_fetcher.timeout_seconds == 30
    assert scientific.web_page_fetcher.max_response_bytes == 4 * 1024 * 1024


@pytest.mark.parametrize("explicit", [False, True])
def test_cli_reuses_deepseek_key_for_official_hosted_search(tmp_path, monkeypatch, explicit):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-deepseek-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-only-tavily-key")
    if explicit:
        monkeypatch.setenv("RESAGENT2_WEB_SEARCH_PROVIDER", "deepseek")
    backend = scientific_application(tmp_path).web_search_backend

    assert isinstance(backend, DeepSeekWebSearchBackend)
    assert backend.model == "deepseek-flash"
    assert backend.max_tokens == 4096
    assert backend.max_uses == 5
    assert isinstance(backend.client, ModelRequestClient)
    assert backend.client.endpoint == "https://api.deepseek.com/anthropic/v1/messages"
    assert backend.client.api_key_env == "DEEPSEEK_API_KEY"
    assert backend.client.api_key_headers == ("x-api-key", "Authorization")
    assert backend.client.extra_headers == {"anthropic-version": "2023-06-01"}
    assert backend.client.timeout_seconds == 60
    assert backend.client.max_response_bytes == 4 * 1024 * 1024


def test_cli_search_model_and_bounds_are_independent_from_agent_model_and_fetch(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-deepseek-key")
    monkeypatch.setenv("RESAGENT2_MODEL", "other-agent-model")
    monkeypatch.setenv("RESAGENT2_WEB_SEARCH_MODEL", "explicit-search-model")
    monkeypatch.setenv("RESAGENT2_WEB_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("RESAGENT2_WEB_SEARCH_TIMEOUT_SECONDS", "11")
    monkeypatch.setenv("RESAGENT2_WEB_MAX_RESPONSE_BYTES", "1234")
    scientific = scientific_application(tmp_path)

    assert scientific.web_search_backend.model == "explicit-search-model"
    assert scientific.web_search_backend.client.timeout_seconds == 11
    assert scientific.web_page_fetcher.timeout_seconds == 7
    assert scientific.web_search_backend.client.max_response_bytes == 1234
    assert scientific.web_page_fetcher.max_response_bytes == 1234


def test_cli_explicit_tavily_uses_its_own_key_and_existing_web_bounds(tmp_path, monkeypatch):
    monkeypatch.setenv("RESAGENT2_WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-deepseek-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-only-tavily-key")
    monkeypatch.setenv("RESAGENT2_WEB_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("RESAGENT2_WEB_SEARCH_TIMEOUT_SECONDS", "11")
    monkeypatch.setenv("RESAGENT2_WEB_MAX_RESPONSE_BYTES", "1234")
    scientific = scientific_application(tmp_path)

    assert isinstance(scientific.web_search_backend, TavilyWebSearchBackend)
    assert scientific.web_search_backend.api_key == "test-only-tavily-key"
    assert scientific.web_search_backend.timeout_seconds == 7
    assert scientific.web_search_backend.max_response_bytes == 1234
    assert scientific.web_page_fetcher.timeout_seconds == 7


def test_cli_off_disables_search_even_when_both_keys_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("RESAGENT2_WEB_SEARCH_PROVIDER", "off")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-deepseek-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-only-tavily-key")
    scientific = scientific_application(tmp_path)

    assert scientific.web_search_backend is None
    assert isinstance(scientific.web_page_fetcher, WebPageFetcher)


@pytest.mark.parametrize("provider,key_env,other_key", [
    ("deepseek", "DEEPSEEK_API_KEY", "TAVILY_API_KEY"),
    ("tavily", "TAVILY_API_KEY", "DEEPSEEK_API_KEY"),
])
@pytest.mark.parametrize("key", [None, "", "   "])
def test_explicit_provider_requires_its_key_before_io_or_client_creation(
    tmp_path, monkeypatch, provider, key_env, other_key, key,
):
    monkeypatch.setenv("RESAGENT2_WEB_SEARCH_PROVIDER", provider)
    monkeypatch.setenv(other_key, "test-only-other-key")
    if key is not None:
        monkeypatch.setenv(key_env, key)
    monkeypatch.setattr(composition, "_client", lambda: pytest.fail("unexpected client creation"))
    monkeypatch.setattr(composition, "ModelRequestClient", lambda **kwargs: pytest.fail("unexpected search client"))
    root = tmp_path / "data"

    with pytest.raises(ValueError, match=f"requires {key_env}"):
        composition.build_application(data_root=root)
    assert not root.exists()


@pytest.mark.parametrize("provider", ["other", "", "DeepSeek"])
def test_unknown_search_provider_fails_before_io_or_client_creation(tmp_path, monkeypatch, provider):
    monkeypatch.setenv("RESAGENT2_WEB_SEARCH_PROVIDER", provider)
    monkeypatch.setattr(composition, "_client", lambda: pytest.fail("unexpected client creation"))
    monkeypatch.setattr(composition, "ModelRequestClient", lambda **kwargs: pytest.fail("unexpected search client"))
    root = tmp_path / "data"

    with pytest.raises(ValueError, match="RESAGENT2_WEB_SEARCH_PROVIDER must be"):
        composition.build_application(data_root=root)
    assert not root.exists()


@pytest.mark.parametrize("name", [
    "RESAGENT2_WEB_TIMEOUT_SECONDS", "RESAGENT2_WEB_MAX_RESPONSE_BYTES",
    "RESAGENT2_WEB_SEARCH_TIMEOUT_SECONDS",
])
@pytest.mark.parametrize("value", ["0", "-1", "invalid", "1.5", ""])
def test_invalid_web_bounds_fail_before_io_or_client_creation(tmp_path, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(composition, "_client", lambda: pytest.fail("unexpected client creation"))
    monkeypatch.setattr(composition, "ModelRequestClient", lambda **kwargs: pytest.fail("unexpected search client"))
    root = tmp_path / "data"
    with pytest.raises(ValueError, match=f"{name} must be"):
        composition.build_application(data_root=root)
    assert not root.exists()
