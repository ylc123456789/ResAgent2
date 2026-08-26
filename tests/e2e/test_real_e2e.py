from types import SimpleNamespace

from resagent2_contracts import Capability, RunStatus, TaskStatus

from e2e.real_e2e import _real_e2e_succeeded


def _run(*, artifact_kinds: set[str]) -> SimpleNamespace:
    task_for = {
        Capability.CODE_MODIFY: "task_code",
        Capability.EXPERIMENT_RUN: "task_experiment",
        Capability.SCIENTIFIC_ANALYZE: "task_analyze",
    }
    tasks = [
        SimpleNamespace(
            id=task_id,
            capability=capability,
            status=TaskStatus.COMPLETED,
            attempts=[SimpleNamespace(number=1)],
        )
        for capability, task_id in task_for.items()
    ]
    owner_for_kind = {
        "code_change": Capability.CODE_MODIFY,
        "experiment_result": Capability.EXPERIMENT_RUN,
        "scientific_decision": Capability.SCIENTIFIC_ANALYZE,
    }
    artifacts = {
        f"artifact_{index}": SimpleNamespace(
            kind=kind,
            task_id=task_for[owner_for_kind[kind]],
        )
        for index, kind in enumerate(sorted(artifact_kinds), start=1)
    }
    return SimpleNamespace(
        status=RunStatus.COMPLETED,
        workflow=SimpleNamespace(tasks=tasks),
        artifacts=artifacts,
    )


def test_real_e2e_accepts_registered_artifacts_for_all_three_steps() -> None:
    run = _run(
        artifact_kinds={"code_change", "experiment_result", "scientific_decision"}
    )

    assert _real_e2e_succeeded(run, code_workspace_changed=False)


def test_real_e2e_accepts_documented_legacy_code_retry_exception() -> None:
    run = _run(artifact_kinds={"experiment_result", "scientific_decision"})

    assert _real_e2e_succeeded(run, code_workspace_changed=True)
    assert not _real_e2e_succeeded(run, code_workspace_changed=False)


def test_real_e2e_never_waives_registered_scientific_evidence() -> None:
    run = _run(artifact_kinds={"experiment_result"})

    assert not _real_e2e_succeeded(run, code_workspace_changed=True)
