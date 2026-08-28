from types import SimpleNamespace

from resagent2_contracts import Capability, RunStatus, TaskStatus

from e2e.real_e2e import _real_e2e_succeeded


def _run(*, artifact_kinds: set[str]) -> SimpleNamespace:
    task_for = {
        Capability.CODE_MODIFY: "task_code",
        Capability.EXPERIMENT_RUN: "task_experiment",
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
        final_opinion=SimpleNamespace(),
        final_report_artifact_id="artifact_final_report",
        workflow=SimpleNamespace(tasks=tasks),
        artifacts=artifacts,
    )


def test_real_e2e_accepts_registered_artifacts_and_opinion() -> None:
    run = _run(artifact_kinds={"code_change", "experiment_result"})

    assert _real_e2e_succeeded(run)


def test_real_e2e_requires_registered_code_evidence() -> None:
    run = _run(artifact_kinds={"experiment_result"})

    assert not _real_e2e_succeeded(run)


def test_real_e2e_never_waives_registered_experiment_evidence() -> None:
    run = _run(artifact_kinds={"code_change"})

    assert not _real_e2e_succeeded(run)


def test_real_e2e_requires_a_final_opinion() -> None:
    run = _run(artifact_kinds={"code_change", "experiment_result"})
    run.final_opinion = None

    assert not _real_e2e_succeeded(run)
