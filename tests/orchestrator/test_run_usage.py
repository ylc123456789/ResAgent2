"""Run reservations survive interrupted calls without replacing live artifacts."""
from datetime import UTC, datetime

import pytest

from resagent2_contracts import ArtifactCandidate, ResearchRequest, RunBudget, RunPermissions
from resagent2_orchestrator import JsonRunStore, ResearchRun
from resagent2_orchestrator.artifacts import ArtifactRegistry, ScientificArtifactRegistration
from resagent2_orchestrator.usage import RunUsagePort
from resagent2_runtime.budget import BudgetExhaustedError, execution_budget, invoke_model


def prepared(tmp_path, *, calls=2):
    store = JsonRunStore(tmp_path / "runs")
    now = datetime.now(UTC)
    run = ResearchRun(
        run_id="run_usage", status="running",
        request=ResearchRequest(goal="Test usage",
            permissions=RunPermissions(), budget=RunBudget(max_llm_calls=calls, timeout_seconds=60)),
        created_at=now, updated_at=now,
    )
    store.save(run)
    return store, run


def test_request_reservation_is_on_disk_before_client_is_called(tmp_path):
    store, run = prepared(tmp_path)
    port = RunUsagePort(run, store)
    class Client:
        def next_action(self):
            saved = JsonRunStore(store.root).load(run.run_id)
            assert list(saved.usage.requests.values()) == ["unknown"]
            return "done"
    with execution_budget(max_llm_calls=2, timeout_seconds=60, usage=port):
        assert invoke_model(Client(), "next_action") == "done"
    saved = store.load(run.run_id)
    assert saved.llm_calls_used == 1
    assert list(saved.usage.requests.values()) == ["succeeded"]


def test_failed_reservation_prevents_dispatch(tmp_path, monkeypatch):
    store, run = prepared(tmp_path)
    port = RunUsagePort(run, store)
    class Client:
        def next_action(self):
            pytest.fail("request must not be sent before durable reservation")
    def fail(_):
        raise OSError("storage unavailable")
    monkeypatch.setattr(store, "save", fail)
    with execution_budget(max_llm_calls=2, timeout_seconds=60, usage=port):
        with pytest.raises(OSError, match="storage unavailable"):
            invoke_model(Client(), "next_action")
    assert JsonRunStore(store.root).load(run.run_id).llm_calls_used == 0


def test_unknown_reservations_are_not_refunded_on_restart(tmp_path):
    store, run = prepared(tmp_path, calls=1)
    RunUsagePort(run, store).charge("interrupted", 0)
    reopened = JsonRunStore(store.root)
    saved = reopened.load(run.run_id)
    assert saved.usage.requests == {"interrupted:0": "unknown"}
    port = RunUsagePort(saved, reopened)
    with execution_budget(max_llm_calls=0, timeout_seconds=60, usage=port):
        with pytest.raises(BudgetExhaustedError):
            invoke_model(object(), "next_action")
    assert reopened.load(run.run_id).usage.requests == {"interrupted:0": "unknown"}


def test_usage_updates_preserve_artifacts_registered_during_invocation(tmp_path):
    store, run = prepared(tmp_path)
    port = RunUsagePort(run, store)
    port.charge("first", 0)
    bridge = ScientificArtifactRegistration(ArtifactRegistry(tmp_path / "artifacts"), store)
    ref = bridge.register_scientific(
        ArtifactCandidate(kind="literature_search", path="papers.md", media_type="text/markdown",
                          summary="Search result", content="A paper"),
        run_id=run.run_id, session_id="session_usage",
    )
    port.complete("first", 0, "succeeded")
    port.charge("second", 0)
    persisted = store.load(run.run_id)
    assert persisted.artifacts[ref.id] == ref
    assert persisted.llm_calls_used == 2
    assert persisted.usage.requests == {"first:0": "succeeded", "second:0": "unknown"}
