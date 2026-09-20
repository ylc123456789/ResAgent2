"""The independent E2E composition root uses production mechanisms."""

from datetime import UTC, datetime

import pytest

from e2e import real_e2e
from resagent2_contracts import RunBudget, WorkRequest, WorkRequestDraft
from resagent2_orchestrator import CompilationError
from resagent2_runtime import (
    DEFAULT_AGENT_CONTEXT_TOKENS,
    AgentAction,
    ContextBudgetExceeded,
    PromptLLMClient,
    ScriptedLLMClient,
)


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
    assert 0 < client.contexts[0].estimated_tokens <= DEFAULT_AGENT_CONTEXT_TOKENS
    with pytest.raises(ContextBudgetExceeded):
        adapter.next_action("x" * (DEFAULT_AGENT_CONTEXT_TOKENS * 4), AgentAction)
    assert len(client.contexts) == 1
    assert adapter.last_attempts == 0


@pytest.mark.parametrize("oversized", [False, True])
def test_compilation_instruction_uses_the_existing_context_budget(monkeypatch, tmp_path, oversized):
    """Actual compiler + composition adapter; no network or scripted judgment claim."""
    repeats = DEFAULT_AGENT_CONTEXT_TOKENS if oversized else 4000
    instructions = "Measure accuracy without changing code." + " detail" * repeats
    draft = {
        "tasks": [{
            "key": "measure", "workflow_agent_kind": "experiment", "instruction": instructions,
        }],
    }
    client = ScriptedLLMClient([draft])
    monkeypatch.setattr(real_e2e, "_new_llm_client", lambda: client)
    controller, _ = real_e2e._build_controller(tmp_path, None)
    request = WorkRequest(
        id="work_review", run_id="run_review", scientific_session_id="session_review",
        request=WorkRequestDraft(objective=instructions, expected_evidence=["accuracy"]),
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    kwargs = dict(
        current=None, registry=controller.registry,
        budget=RunBudget(max_tasks=1, max_attempts_per_task=1, max_llm_calls=6, timeout_seconds=60),
    )
    if oversized:
        with pytest.raises(CompilationError) as caught:
            controller.compiler.compile(request, **kwargs)
        cause = caught.value.__cause__
        while cause is not None and not isinstance(cause, ContextBudgetExceeded):
            cause = cause.__cause__
        assert isinstance(cause, ContextBudgetExceeded)
        assert caught.value.llm_calls == 0
        assert len(client.contexts) == 0
        assert controller.compiler._client.last_attempts == 0
    else:
        result = controller.compiler.compile(request, **kwargs)
        assert result.llm_calls == len(client.contexts) == 1
        context = client.contexts[0]
        assert context.included_sections == ["system", "compiler_request"]
        assert 4096 < context.estimated_tokens <= DEFAULT_AGENT_CONTEXT_TOKENS
        assert instructions in context.text
