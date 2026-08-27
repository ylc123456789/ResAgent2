from resagent2_contracts import ModuleStatus
from resagent2_orchestrator.adapters import (
    LegacyScientificAnalyzeAdapter,
)


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
