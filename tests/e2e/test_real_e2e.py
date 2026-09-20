from types import SimpleNamespace

from resagent2_contracts import WorkflowAgentKind, RunStatus, TaskStatus

from e2e.real_e2e import _new_llm_client, _real_e2e_succeeded


def _run(*, artifact_kinds: set[str]) -> SimpleNamespace:
    task_for = {
        WorkflowAgentKind.CODING: "task_code",
        WorkflowAgentKind.EXPERIMENT: "task_experiment",
    }
    tasks = [
        SimpleNamespace(
            id=task_id,
            workflow_agent_kind=capability,
            status=TaskStatus.COMPLETED,
            attempts=[SimpleNamespace(number=1)],
        )
        for capability, task_id in task_for.items()
    ]
    owner_for_kind = {
        "code_change": WorkflowAgentKind.CODING,
        "experiment_result": WorkflowAgentKind.EXPERIMENT,
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


def test_real_e2e_uses_the_configured_current_model(monkeypatch) -> None:
    monkeypatch.delenv("RESAGENT2_MODEL", raising=False)
    assert _new_llm_client().model == "deepseek-v4-flash"

    monkeypatch.setenv("RESAGENT2_MODEL", "deepseek-v4-pro")
    assert _new_llm_client().model == "deepseek-v4-pro"


def test_direct_agent_dataset_material_is_a_frozen_registered_snapshot(tmp_path):
    from e2e.real_e2e import _dataset_materials
    from resagent2_components import RegisteredArtifactReader, ResourceLayout, read_artifact_json
    layout = ResourceLayout(resource_root=tmp_path / "resources")
    layout.dataset_root.mkdir(parents=True)
    catalog = layout.dataset_root / "catalog.json"
    catalog.write_text('{"demo": "demo"}')
    refs = _dataset_materials(tmp_path, layout, "run_direct")
    catalog.write_text("{}")
    data = read_artifact_json(RegisteredArtifactReader(refs, run_id="run_direct"), refs[0].id)
    assert data["datasets"][0]["dataset_id"] == "demo"
    assert refs[0].producer.value == "orchestrator"
