"""The two existing composition roots explicitly choose the same backends."""

import pytest

from resagent2_components import (
    ArxivLiteratureBackend,
    MultiSourceLiteratureBackend,
    OpenAlexLiteratureBackend,
)
from resagent2_components import ResourceLayout
from resagent2_cli import composition
from resagent2_runtime import InMemorySessionStore


@pytest.mark.parametrize("api_key", [None, "private-test-key"])
def test_cli_and_e2e_wire_same_literature_backends(tmp_path, monkeypatch, api_key):
    from e2e import real_e2e

    if api_key:
        monkeypatch.setenv("OPENALEX_API_KEY", api_key)
    else:
        monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    monkeypatch.setattr(composition, "_client", lambda: object())
    monkeypatch.setattr(composition, "_compiler_client", lambda **kwargs: object())
    monkeypatch.setattr(real_e2e, "_new_llm_client", lambda: object())
    app = composition.build_application(data_root=tmp_path / "cli")
    e2e_agent = real_e2e._scientific_agent(
        None, InMemorySessionStore(), ResourceLayout.from_env(data_root=tmp_path / "e2e"),
    )
    for agent in (app.controller.scientific_port, e2e_agent):
        backend = agent.literature_backend
        assert isinstance(backend, MultiSourceLiteratureBackend)
        assert len(backend.backends) == 2
        assert isinstance(backend.backends[0], ArxivLiteratureBackend)
        assert isinstance(backend.backends[1], OpenAlexLiteratureBackend)
        assert backend.backends[1]._api_key == api_key
