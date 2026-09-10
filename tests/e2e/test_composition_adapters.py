"""The independent E2E composition root uses production mechanisms."""

from datetime import UTC, datetime

import pytest

from e2e import real_e2e
from resagent2_contracts import RunBudget, WorkRequest, WorkRequestDraft
from resagent2_orchestrator import CompilationError
from resagent2_runtime import AgentAction, ContextBudgetExceeded, PromptLLMClient, ScriptedLLMClient


def test_real_e2e_compiler_uses_budgeted_adapter(monkeypatch, tmp_path):
    client = ScriptedLLMClient([{"tool": "finish"}])
    monkeypatch.setattr(real_e2e, "_new_llm_client", lambda: client)
    controller, _ = real_e2e._build_controller(tmp_path, None)
    layout = controller.scientific_port.resource_layout
    assert all(
        binding.port.resource_layout is layout
        for binding in controller.scheduler.bindings.values()
    )
    assert controller.dataset_ref_source.dataset_root == layout.dataset_root.resolve()
    adapter = controller.compiler._client
    assert isinstance(adapter, PromptLLMClient)

    adapter.next_action("Compile this objective", AgentAction)
    assert client.contexts[0].included_sections == ["system", "compiler_request"]
    assert 0 < client.contexts[0].estimated_tokens <= 4096
    with pytest.raises(ContextBudgetExceeded):
        adapter.next_action("x" * 20000, AgentAction)
    assert len(client.contexts) == 1
    assert adapter.last_attempts == 0


@pytest.mark.parametrize("oversized", [False, True])
def test_full_review_semantics_use_the_existing_context_budget(monkeypatch, tmp_path, oversized):
    """Actual compiler + composition adapter; no network or scripted judgment claim."""
    instructions = "Measure accuracy without changing code." + (" detail" * 4000 if oversized else "")
    draft = {
        "summary": "Measure", "rationale": "Obtain evidence",
        "tasks": [{
            "key": "measure", "capability": "experiment_run", "goal": "Run measurement",
            "constraints": ["Use registered data only"],
            "inputs": {"capability": "experiment_run", "instructions": instructions},
        }],
    }
    client = ScriptedLLMClient([draft, {"accepted": True}])
    monkeypatch.setattr(real_e2e, "_new_llm_client", lambda: client)
    controller, _ = real_e2e._build_controller(tmp_path, None)
    request = WorkRequest(
        id="work_review", run_id="run_review", scientific_session_id="session_review",
        request=WorkRequestDraft(objective="Measure the method", expected_evidence=["accuracy"]),
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    kwargs = dict(
        current=None, registry=controller.registry,
        budget=RunBudget(max_tasks=1, max_attempts_per_task=1, max_llm_calls=6, timeout_seconds=60),
    )
    if oversized:
        with pytest.raises(CompilationError) as caught:
            controller.compiler.compile(request, **kwargs)
        assert isinstance(caught.value.__cause__, ContextBudgetExceeded)
        assert caught.value.llm_calls == 1
        assert len(client.contexts) == 1  # draft only; oversized review never calls provider
        assert controller.compiler._client.last_attempts == 0
    else:
        result = controller.compiler.compile(request, **kwargs)
        assert result.llm_calls == 2
        assert len(client.contexts) == 2
        review = client.contexts[1]
        assert review.included_sections == ["system", "compiler_request"]
        assert 0 < review.estimated_tokens <= 4096
        assert "  inputs=" in review.text and instructions in review.text
        assert "  constraints=" in review.text and "Use registered data only" in review.text
        assert "Code-level verification does not replace formal experiment delivery" in review.text
