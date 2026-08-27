from resagent2_contracts import ModuleStatus
from resagent2_orchestrator.adapters import (
    LegacyExperimentAdapter,
    LegacyScientificAnalyzeAdapter,
)


def test_experiment_adapter_maps_agent_state_statuses() -> None:
    completed = LegacyExperimentAdapter.from_result(
        {"status": "completed", "final_summary": "ok", "structured_result": {"accuracy": 0.9}}
    )
    assert completed.status == ModuleStatus.COMPLETED
    assert completed.payload == {"accuracy": 0.9}

    warned = LegacyExperimentAdapter.from_result(
        {"status": "completed_with_failures", "final_summary": "partial"}
    )
    assert warned.status == ModuleStatus.COMPLETED_WITH_WARNINGS
    assert len(warned.warnings) == 1

    assert LegacyExperimentAdapter.from_result(
        {"status": "blocked", "summary": "need code"}
    ).status == ModuleStatus.BLOCKED
    assert LegacyExperimentAdapter.from_result(
        {"status": "failed", "summary": "crashed"}
    ).status == ModuleStatus.FAILED


def test_scientific_adapter_maps_decision_statuses() -> None:
    completed = LegacyScientificAnalyzeAdapter.from_result(
        {"summary": "analyzed", "conclusion": {"status": "supported"}, "confidence": "high"}
    )
    assert completed.status == ModuleStatus.COMPLETED
    assert completed.payload["conclusion"] == {"status": "supported"}
    assert completed.payload["confidence"] == "high"

    needs = LegacyScientificAnalyzeAdapter.from_result(
        {"summary": "analyzed", "needs_user_input": ["which dataset?"]}
    )
    assert needs.status == ModuleStatus.NEEDS_USER_INPUT
    assert needs.question is not None
