"""Keep ordinary tests independent of host hardware and external environments."""

import pytest


@pytest.fixture(autouse=True)
def _environment_information(monkeypatch):
    # The probe itself is exercised in test_environment_info. Agent/tool tests
    # replace this observation when they need a particular environment.
    monkeypatch.setattr(
        "resagent2_components.environment.inspect_environment",
        lambda prefix, conda_exe: {
            "observed_at": "2026-09-28T00:00:00+00:00",
            "prefix": str(prefix),
            "runtime": {"status": "unavailable", "error": "test fixture"},
            "nvidia": {"status": "unavailable", "error": "test fixture"},
        },
    )
