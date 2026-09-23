"""Deployment output headroom must not expand module input or retry policies."""

from datetime import UTC, datetime
import httpx
import json

import pytest

from resagent2_cli import composition
from resagent2_coding.models import CodingAction
from resagent2_contracts import ExecutionLimits, WorkBrief, WorkRequest, WorkRequestDraft
from resagent2_runtime.budget import execution_budget


@pytest.fixture
def model_config_scope():
    with execution_budget(max_llm_calls=10, timeout_seconds=900):
        yield
from resagent2_experiment.models import ExperimentAction
from resagent2_orchestrator import LLMWorkflowCompiler
from resagent2_orchestrator.compiler import CompilationDraft
from resagent2_runtime import DEFAULT_AGENT_CONTEXT_TOKENS, ComposedContext, ScriptedLLMClient
from resagent2_scientific.models import ScientificAction


@pytest.fixture
def defaults(monkeypatch):
    for name in (
        "CONTEXT_WINDOW", "RESERVED_OUTPUT_TOKENS", "CONTEXT_SAFETY_MARGIN_TOKENS",
        "LLM_TIMEOUT_SECONDS", "MODEL", "API_BASE", "API_KEY_ENV", "LLM_TRACE_LEVEL",
        "LLM_TRACE_DIR", "SCIENTIFIC_CONTEXT_TOKENS", "CODING_CONTEXT_TOKENS",
        "EXPERIMENT_CONTEXT_TOKENS", "COMPILER_CONTEXT_TOKENS", "INTERPRETER_CONTEXT_TOKENS",
    ):
        monkeypatch.delenv(f"RESAGENT2_{name}", raising=False)
    monkeypatch.setenv("RESAGENT2_API_KEY_ENV", "TEST_CONFIG_KEY")
    monkeypatch.setenv("TEST_CONFIG_KEY", "test-only")


@pytest.mark.parametrize("component,action_type,limit", [
    ("scientific", ScientificAction, 128_000),
    ("coding", CodingAction, 128_000),
    ("experiment", ExperimentAction, 128_000),
    ("compiler", CompilationDraft, 128_000),
    ("interpreter", WorkBrief, 128_000),
])
def test_output_headroom_preserves_every_module_input_limit(defaults, component, action_type, limit):
    client = composition._client()
    assert client.model_profile.context_window == 1_000_000
    assert client.model_profile.reserved_output_tokens == 256_000
    assert client.model_profile.safety_margin_tokens == 1024
    assert client.timeout_seconds == 600
    assert composition._component_context_limit(component) == limit
    assert client.context_budget(action_type, limit) == limit


@pytest.mark.parametrize("component", ["compiler", "interpreter"])
def test_real_e2e_translation_uses_the_shared_cli_default(defaults, monkeypatch, tmp_path, component):
    from e2e import real_e2e

    monkeypatch.setattr(real_e2e, "_new_llm_client", lambda: ScriptedLLMClient([]))
    controller, _ = real_e2e._build_controller(tmp_path, None)

    assert getattr(controller, component)._client._max_context_tokens == DEFAULT_AGENT_CONTEXT_TOKENS
    assert composition._component_context_limit(component) == DEFAULT_AGENT_CONTEXT_TOKENS


def test_cli_interpreter_has_independent_client_and_context_limit(defaults, monkeypatch, tmp_path):
    monkeypatch.setenv("RESAGENT2_INTERPRETER_CONTEXT_TOKENS", "16000")
    app = composition.build_application(data_root=tmp_path)
    compiler = app.controller.compiler._client
    interpreter = app.controller.interpreter._client

    assert interpreter is not compiler
    assert interpreter._client is not compiler._client
    assert interpreter._client.model_profile == compiler._client.model_profile
    assert interpreter._max_context_tokens == 16000
    assert interpreter._section_name == "interpreter_request"
    assert compiler._max_context_tokens == DEFAULT_AGENT_CONTEXT_TOKENS


@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_invalid_interpreter_context_limit_fails_before_network(defaults, monkeypatch, value):
    monkeypatch.setenv("RESAGENT2_INTERPRETER_CONTEXT_TOKENS", value)
    with pytest.raises(ValueError):
        composition._component_context_limit("interpreter")


def test_real_e2e_interpreter_honors_its_context_limit(defaults, monkeypatch, tmp_path):
    from e2e import real_e2e

    monkeypatch.setattr(real_e2e, "_new_llm_client", lambda: ScriptedLLMClient([]))
    monkeypatch.setenv("RESAGENT2_INTERPRETER_CONTEXT_TOKENS", "16000")
    controller, _ = real_e2e._build_controller(tmp_path, None)

    assert controller.interpreter._client._max_context_tokens == 16000
    assert controller.compiler._client._max_context_tokens == DEFAULT_AGENT_CONTEXT_TOKENS


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "deepseek-v4-pro"])
def test_cli_wire_uses_configured_output_cap_not_an_input_cap(defaults, monkeypatch, model, model_config_scope):
    monkeypatch.setenv("RESAGENT2_MODEL", model)
    seen = []

    def respond(request, *, timeout):
        seen.append((json.loads(request.content), timeout))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"tool":"finish"}'}}]}, request=request)

    monkeypatch.setattr("resagent2_runtime.llm.send_request", respond)
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
    assert client.context_budget(CodingAction, composition._component_context_limit("coding")) == 12000


@pytest.mark.parametrize("key,value", [
    ("RESERVED_OUTPUT_TOKENS", "0"), ("RESERVED_OUTPUT_TOKENS", "invalid"),
    ("LLM_TIMEOUT_SECONDS", "0"), ("LLM_TIMEOUT_SECONDS", "invalid"),
    ("CONTEXT_WINDOW", "65536"),  # A stale small capacity cannot fit the new reservation.
])
def test_invalid_capacity_configuration_fails_before_network(defaults, monkeypatch, key, value):
    monkeypatch.setenv(f"RESAGENT2_{key}", value)
    monkeypatch.setattr("resagent2_runtime.llm.send_request", lambda *a, **k: pytest.fail("no network"))
    with pytest.raises(ValueError):
        composition._client()


def test_full_cli_compilation_uses_new_defaults_for_one_draft(defaults, monkeypatch, tmp_path, model_config_scope):
    monkeypatch.setenv("RESAGENT2_LLM_TRACE_LEVEL", "full")
    monkeypatch.setenv("RESAGENT2_LLM_TRACE_DIR", str(tmp_path / "trace"))
    replies = iter([
        {"tasks": [{
            "key": "measure", "workflow_agent_kind": "experiment", "instruction": "Measure the method",
        }]},
    ])
    requests = []

    def respond(request, *, timeout):
        requests.append((json.loads(request.content), timeout))
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(next(replies))}}],
            "usage": {"completion_tokens": 100},
        }, request=request)

    monkeypatch.setattr("resagent2_runtime.llm.send_request", respond)
    compiler_limit = composition._component_context_limit("compiler")
    compiler = LLMWorkflowCompiler(composition._compiler_client(max_context_tokens=compiler_limit))
    now = datetime.now(UTC)
    result = compiler.compile(WorkRequest(id='work_config', run_id='run_config', scientific_session_id='session_config', request=WorkRequestDraft(objective='Measure the method', expected_evidence=['Measured result']), created_at=now, updated_at=now), current=None, registry=composition._registry(), limits=ExecutionLimits())
    assert result.llm_calls == len(requests) == 1
    assert all(body["max_tokens"] == 256_000 and 599 < timeout <= 600 for body, timeout in requests)
    rows = [json.loads(line) for line in (tmp_path / "trace/llm_traces.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    for row in rows:
        assert row["request_max_tokens"] == 256_000
        assert row["included_sections"] == ["system", "compiler_request"]
        assert 0 < row["estimated_tokens"] <= compiler_limit
        assert len(row["attempts"]) == row["retry_number"] + 1 == 1
