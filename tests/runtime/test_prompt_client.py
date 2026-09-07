"""Plain-prompt callers share runtime budgets and provider accounting."""

import json
from unittest import mock
from urllib.error import URLError

import pytest
from pydantic import BaseModel

from resagent2_runtime import (
    AgentAction, ContextBudgetExceeded, ModelProfile, OpenAICompatibleClient,
    PromptLLMClient, ScriptedLLMClient,
)


def _adapter(client, *, limit=512):
    return PromptLLMClient(
        client, system_prompt="Return one action.", max_context_tokens=limit,
    )


def test_plain_prompt_uses_required_context_without_agent_loop():
    client = ScriptedLLMClient([{"tool": "finish"}])
    adapter = _adapter(client)
    # These hooks are optional for minimal clients.
    adapter.set_trace_context(run_id="run_plain")
    adapter.set_attempt_limit(1)

    assert adapter.next_action("Complete this request", AgentAction) == {"tool": "finish"}
    context = client.contexts[0]
    assert context.included_sections == ["system", "request"]
    assert context.omitted_sections == []
    assert "Complete this request" in context.text
    assert 0 < context.estimated_tokens <= 512
    assert adapter.last_attempts == 1


def test_plain_prompt_accepts_a_schema_without_agent_action_fields():
    class Decision(BaseModel):
        accepted: bool

    response = Decision(accepted=True)
    adapter = _adapter(ScriptedLLMClient([response]))
    assert adapter.next_action("Review this proposal", Decision) is response

    instruction = OpenAICompatibleClient._action_instruction(Decision)
    schema = json.loads(instruction.split("\n", 1)[1])
    assert schema["required"] == ["accepted"]
    assert "tool" not in schema["properties"]


def test_plain_prompt_rejects_invalid_component_limit():
    with pytest.raises(ValueError, match="must be positive"):
        _adapter(ScriptedLLMClient([]), limit=0)


def test_over_budget_prompt_does_not_call_provider_or_reuse_previous_count():
    client = ScriptedLLMClient([{"tool": "finish"}])
    adapter = _adapter(client, limit=64)
    adapter.next_action("Complete", AgentAction)
    assert adapter.last_attempts == 1

    with pytest.raises(ContextBudgetExceeded):
        adapter.next_action("x" * 1000, AgentAction)

    assert len(client.contexts) == 1
    assert adapter.last_attempts == 0


def test_plain_prompt_respects_provider_model_capacity(monkeypatch):
    client = OpenAICompatibleClient(
        model="test", api_base="https://example.invalid/v1", api_key_env="NO_KEY",
        model_profile=ModelProfile(
            context_window=1000, reserved_output_tokens=100, safety_margin_tokens=100,
        ),
    )
    available = client.context_budget(AgentAction, component_limit=4096)
    assert 0 < available < 1000
    monkeypatch.setattr(
        client, "next_action", lambda *_: pytest.fail("must reject before provider"),
    )

    adapter = _adapter(client, limit=4096)
    with pytest.raises(ContextBudgetExceeded):
        adapter.next_action("x" * (4 * (available + 1)), AgentAction)
    assert adapter.last_attempts == 0


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({
            "choices": [{"message": {"content": '{"tool": "finish"}'}}],
        }).encode()


@pytest.mark.parametrize("succeeds", [True, False])
def test_plain_prompt_forwards_retry_limit_trace_and_real_attempt_count(
    monkeypatch, tmp_path, succeeds,
):
    monkeypatch.setenv("PROMPT_TEST_KEY", "dummy-test-key")
    client = OpenAICompatibleClient(
        model="test", api_base="https://example.invalid/v1", api_key_env="PROMPT_TEST_KEY",
        trace_dir=tmp_path / "traces", trace_level="full",
    )
    adapter = _adapter(client)
    adapter.set_trace_context(agent="plain_caller", run_id="run_plain", step="review")
    adapter.set_attempt_limit(2)
    responses = [URLError("transient"), _Response() if succeeds else URLError("again")]

    with (
        mock.patch("resagent2_runtime.llm.time.sleep"),
        mock.patch("resagent2_runtime.llm.urlopen", side_effect=responses) as request,
    ):
        if succeeds:
            assert adapter.next_action("Complete", AgentAction) == {"tool": "finish"}
        else:
            with pytest.raises(RuntimeError, match="after 2 attempts"):
                adapter.next_action("Complete", AgentAction)
    assert request.call_count == adapter.last_attempts == 2
    records = [
        json.loads(line) for line in (tmp_path / "traces" / "llm_traces.jsonl")
        .read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["run_id"] == "run_plain"
    assert records[0]["agent"] == "plain_caller"
    assert records[0]["step"] == "review"
    assert records[0]["retry_number"] == 1
    assert records[0]["included_sections"] == ["system", "request"]
    assert records[0]["estimated_tokens"] > 0
    assert "Complete" in records[0]["request_text"]

    # The previous two HTTP attempts must not leak into a pre-call rejection.
    with pytest.raises(ContextBudgetExceeded):
        adapter.next_action("x" * 10000, AgentAction)
    assert adapter.last_attempts == 0
    assert client.last_attempts == 2
