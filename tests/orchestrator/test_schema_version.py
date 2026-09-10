"""A schema break rejects old runs without rewriting their audit records."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from resagent2_contracts import ResearchRequest, RunBudget, RunStatus
from resagent2_orchestrator import JsonRunStore, ResearchRun


def test_old_run_rejected_without_rewriting_file(tmp_path):
    now = datetime.now(UTC)
    run = ResearchRun(
        run_id="run_schema", status=RunStatus.RUNNING,
        request=ResearchRequest(goal="Version boundary", budget=RunBudget(
            max_tasks=1, max_attempts_per_task=1, max_llm_calls=1, timeout_seconds=60,
        )),
        created_at=now, updated_at=now,
    )
    store = JsonRunStore(tmp_path)
    store.save(run)
    assert store.load(run.run_id) == run

    path = tmp_path / "run_schema.json"
    legacy = path.read_bytes().replace(b'"6.0"', b'"5.0"')
    path.write_bytes(legacy)
    with pytest.raises(ValidationError):
        store.load(run.run_id)
    assert path.read_bytes() == legacy
