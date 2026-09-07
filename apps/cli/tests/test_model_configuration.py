"""Deployment output headroom must not expand module input or retry policies."""

from datetime import UTC, datetime
from io import BytesIO
import json

import pytest

from resagent2_cli import composition
from resagent2_coding.models import CodeModifyAction
from resagent2_contracts import RunBudget, WorkRequest, WorkRequestDraft
from resagent2_experiment.models import ExperimentAction
from resagent2_orchestrator import LLMWorkflowCompiler
from resagent2_orchestrator.compiler import CompilationDraft, CompilationReview
from resagent2_runtime import ComposedContext
from resagent2_scientific.models import ScientificAction


@pytest.fixture
def defaults(monkeypatch):
    for name in (
        "CONTEXT_WINDOW", "RESERVED_OUTPUT_TOKENS", "CONTEXT_SAFETY_MARGIN_TOKENS",
        "LLM_TIMEOUT_SECONDS", "MODEL", "API_BASE", "API_KEY_ENV", "LLM_TRACE_LEVEL",
        "LLM_TRACE_DIR", "SCIENTIFIC_CONTEXT_TOKENS", "CODING_CONTEXT_TOKENS",
        "EXPERIMENT_CONTEXT_TOKENS", "COMPILER_CONTEXT_TOKENS",
    ):
        monkeypatch.delenv(f"RESAGENT2_{name}", raising=False)
    monkeypatch.setenv("RESAGENT2_API_KEY_ENV", "TEST_CONFIG_KEY")
    monkeypatch.setenv("TEST_CONFIG_KEY", "test-only")


@pytest.mark.parametrize("component,action_type,limit", [
    ("scientific", ScientificAction, 8192),
    ("coding", CodeModifyAction, 8192),
    ("experiment", ExperimentAction, 8192),
    ("compiler", CompilationDraft, 4096),
    ("compiler", CompilationReview, 4096),
])
def test_output_headroom_preserves_every_module_input_limit(defaults, component, action_type, limit):
    client = composition._client()
    assert client.model_profile.context_window == 1_000_000
    assert client.model_profile.reserved_output_tokens == 256_000
    assert client.model_profile.safety_margin_tokens == 1024
    assert client.timeout_seconds == 600
    assert composition._component_context_limit(component) == limit
    assert client.context_budget(action_type, limit) == limit


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "deepseek-v4-pro"])
def test_cli_wire_uses_configured_output_cap_not_an_input_cap(defaults, monkeypatch, model):
    monkeypatch.setenv("RESAGENT2_MODEL", model)
    seen = []

    def respond(request, *, timeout):
        seen.append((json.loads(request.data), timeout))
        return BytesIO(json.dumps({"choices": [{"message": {"content": '{"tool":"finish"}'}}]}).encode())

    monkeypatch.setattr("resagent2_runtime.llm.urlopen", respond)
    client = composition._client()
    client.next_action(ComposedContext(
        text="Return JSON", included_sections=["system"], omitted_sections=[], estimated_tokens=3,
    ), ScientificAction)
    assert len(seen) == client.last_attempts == 1
    body, timeout = seen[0]
    assert body["max_tokens"] == 256_000
    assert body["model"] == model
    assert timeout == 600
    assert "thinking" not in body and "reasoning_effort" not in body


def test_custom_model_can_override_capacity_output_and_timeout(defaults, monkeypatch):
    for key, value in {
        "MODEL": "custom-model", "CONTEXT_WINDOW": "32000",
        "RESERVED_OUTPUT_TOKENS": "3000", "LLM_TIMEOUT_SECONDS": "90",
        "CODING_CONTEXT_TOKENS": "12000",
    }.items():
        monkeypatch.setenv(f"RESAGENT2_{key}", value)
    client = composition._client()
    assert client.model == "custom-model"
    assert client.model_profile.context_window == 32000
    assert client.model_profile.reserved_output_tokens == 3000
    assert client.timeout_seconds == 90
    assert client.context_budget(CodeModifyAction, composition._component_context_limit("coding")) == 12000


@pytest.mark.parametrize("key,value", [
    ("RESERVED_OUTPUT_TOKENS", "0"), ("RESERVED_OUTPUT_TOKENS", "invalid"),
    ("LLM_TIMEOUT_SECONDS", "0"), ("LLM_TIMEOUT_SECONDS", "invalid"),
    ("CONTEXT_WINDOW", "65536"),  # A stale small capacity cannot fit the new reservation.
])
def test_invalid_capacity_configuration_fails_before_network(defaults, monkeypatch, key, value):
    monkeypatch.setenv(f"RESAGENT2_{key}", value)
    monkeypatch.setattr("resagent2_runtime.llm.urlopen", lambda *a, **k: pytest.fail("no network"))
    with pytest.raises(ValueError):
        composition._client()


def test_full_cli_compilation_uses_new_defaults_for_draft_and_review(defaults, monkeypatch, tmp_path):
    monkeypatch.setenv("RESAGENT2_LLM_TRACE_LEVEL", "full")
    monkeypatch.setenv("RESAGENT2_LLM_TRACE_DIR", str(tmp_path / "trace"))
    replies = iter([
        {"summary": "Measure", "rationale": "Collect evidence", "tasks": [{
            "key": "measure", "capability": "experiment_run", "goal": "Measure the method",
            "inputs": {"capability": "experiment_run", "instructions": "Measure the method"},
        }]},
        {"accepted": True},
    ])
    requests = []

    def respond(request, *, timeout):
        requests.append((json.loads(request.data), timeout))
        return BytesIO(json.dumps({
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(next(replies))}}],
            "usage": {"completion_tokens": 100},
        }).encode())

    monkeypatch.setattr("resagent2_runtime.llm.urlopen", respond)
    compiler = LLMWorkflowCompiler(composition._compiler_client(max_context_tokens=4096))
    now = datetime.now(UTC)
    result = compiler.compile(WorkRequest(
        id="work_config", run_id="run_config", scientific_session_id="session_config",
        request=WorkRequestDraft(objective="Measure the method", expected_evidence=["Measured result"]),
        created_at=now, updated_at=now,
    ), current=None, registry=composition._registry(), budget=RunBudget(
        max_tasks=1, max_attempts_per_task=1, max_llm_calls=2, timeout_seconds=60,
    ), remaining_calls=2)
    assert result.llm_calls == len(requests) == 2
    assert all(body["max_tokens"] == 256_000 and timeout == 600 for body, timeout in requests)
    rows = [json.loads(line) for line in (tmp_path / "trace/llm_traces.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    for row in rows:
        assert row["request_max_tokens"] == 256_000
        assert row["included_sections"] == ["system", "compiler_request"]
        assert 0 < row["estimated_tokens"] <= 4096
        assert len(row["attempts"]) == row["retry_number"] + 1 == 1
    assert rows[1]["step"] == "review"
