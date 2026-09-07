"""The independent E2E composition root uses production mechanisms."""

import pytest

from e2e import real_e2e
from resagent2_runtime import AgentAction, ContextBudgetExceeded, PromptLLMClient, ScriptedLLMClient


def test_real_e2e_compiler_uses_budgeted_adapter(monkeypatch, tmp_path):
    client = ScriptedLLMClient([{"tool": "finish"}])
    monkeypatch.setattr(real_e2e, "_new_llm_client", lambda: client)
    controller, _ = real_e2e._build_controller(tmp_path, None)
    adapter = controller.compiler._client
    assert isinstance(adapter, PromptLLMClient)

    adapter.next_action("Compile this objective", AgentAction)
    assert client.contexts[0].included_sections == ["system", "compiler_request"]
    assert 0 < client.contexts[0].estimated_tokens <= 4096
    with pytest.raises(ContextBudgetExceeded):
        adapter.next_action("x" * 20000, AgentAction)
    assert len(client.contexts) == 1
    assert adapter.last_attempts == 0
