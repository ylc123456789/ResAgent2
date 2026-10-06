"""CLI optionally composes search while independently providing page fetching."""

import pytest

from resagent2_cli import composition
from resagent2_components import TavilyWebSearchBackend, WebPageFetcher


@pytest.fixture
def configured_clients(monkeypatch):
    monkeypatch.setattr(composition, "_client", lambda: object())
    monkeypatch.setattr(composition, "_compiler_client", lambda **kwargs: object())
    for name in (
        "TAVILY_API_KEY", "RESAGENT2_WEB_TIMEOUT_SECONDS", "RESAGENT2_WEB_MAX_RESPONSE_BYTES",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("api_key", [None, "test-only-key"])
def test_cli_search_provider_is_optional_but_page_fetch_remains_available(
    tmp_path, monkeypatch, configured_clients, api_key,
):
    if api_key is not None:
        monkeypatch.setenv("TAVILY_API_KEY", api_key)
    scientific = composition.build_application(data_root=tmp_path / "data").controller.scientific_port

    assert isinstance(scientific.web_page_fetcher, WebPageFetcher)
    assert scientific.web_page_fetcher.timeout_seconds == 30
    assert scientific.web_page_fetcher.max_response_bytes == 4 * 1024 * 1024
    if api_key is None:
        assert scientific.web_search_backend is None
    else:
        assert isinstance(scientific.web_search_backend, TavilyWebSearchBackend)
        assert scientific.web_search_backend.api_key == api_key


def test_cli_applies_deployment_web_bounds_to_both_components(tmp_path, monkeypatch, configured_clients):
    monkeypatch.setenv("TAVILY_API_KEY", "test-only-key")
    monkeypatch.setenv("RESAGENT2_WEB_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("RESAGENT2_WEB_MAX_RESPONSE_BYTES", "1234")
    scientific = composition.build_application(data_root=tmp_path / "data").controller.scientific_port

    for component in (scientific.web_search_backend, scientific.web_page_fetcher):
        assert component.timeout_seconds == 7
        assert component.max_response_bytes == 1234


@pytest.mark.parametrize("name", [
    "RESAGENT2_WEB_TIMEOUT_SECONDS", "RESAGENT2_WEB_MAX_RESPONSE_BYTES",
])
@pytest.mark.parametrize("value", ["0", "-1", "invalid", "1.5", ""])
def test_invalid_web_bounds_fail_before_io_or_client_creation(tmp_path, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(composition, "_client", lambda: pytest.fail("unexpected client creation"))
    root = tmp_path / "data"
    with pytest.raises(ValueError, match=f"{name} must be"):
        composition.build_application(data_root=root)
    assert not root.exists()
