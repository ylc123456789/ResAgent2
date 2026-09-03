"""Cross-layer recovery: client bad-JSON exhausts retries, loop returns retryable.

The pieces (client bounded retry, loop TOOL_FAILED, scheduler attempt retry) are
each tested in isolation elsewhere; this test verifies the two runtime links in
the chain — the client and the loop — do not break between them.
"""

import json
from unittest import mock

from resagent2_contracts import (
    AgentOwner,
    Capability,
    CodeUnderstandInput,
    ErrorCode,
    ModuleStatus,
    ModuleTaskRequest,
    TaskBudget,
)
from resagent2_runtime import (
    AgentDefinition,
    AgentLoop,
    AllowListPermissionPolicy,
    CompletionDecision,
    ContextSection,
    FinishCandidate,
    FinishTool,
    InMemorySessionStore,
    OpenAICompatibleClient,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class _AcceptFinish:
    def evaluate(self, state, candidate: FinishCandidate | None) -> CompletionDecision:
        if candidate is None:
            return CompletionDecision(complete=False)
        return CompletionDecision(complete=True, summary="done")


def _context(request, state) -> list[ContextSection]:
    return [
        ContextSection(name="task", content=request.goal, priority=100, required=True)
    ]


def test_bad_json_exhausts_client_and_loop_returns_retryable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TEST_LLM_KEY", "dummy")
    client = OpenAICompatibleClient(
        model="test-model",
        api_base="https://example.com/v1",
        api_key_env="TEST_LLM_KEY",
        trace_dir=tmp_path / "traces",
        trace_level="full",
    )
    bad = _FakeResponse({"choices": [{"message": {"content": "not valid json"}}]})
    definition = AgentDefinition(
        name="recovery",
        owner=AgentOwner.CODING,
        system_prompt="Use the provided tools only.",
        tools=(FinishTool(),),
        llm_client=client,
        context_builder=_context,
        permission_policy=AllowListPermissionPolicy({"finish"}),
        completion_check=_AcceptFinish(),
    )
    request = ModuleTaskRequest(
        run_id="run_r",
        task_id="task_r",
        attempt_number=1,
        capability=Capability.CODE_UNDERSTAND,
        goal="exercise recovery",
        inputs=CodeUnderstandInput(question="q"),
        budget=TaskBudget(max_steps=5, max_llm_calls=5, timeout_seconds=60),
    )

    with (
        mock.patch("resagent2_runtime.llm.time.sleep"),
        mock.patch("resagent2_runtime.llm.urlopen", return_value=bad),
    ):
        result = AgentLoop(store=InMemorySessionStore()).run(
            definition, request, session_id="session_recovery"
        )

    assert result.status == ModuleStatus.FAILED
    assert result.error is not None
    assert result.error.code == ErrorCode.TOOL_FAILED
    assert result.error.retryable is True
    assert client.last_attempts == 3

    trace_file = tmp_path / "traces" / "llm_traces.jsonl"
    record = json.loads(trace_file.read_text(encoding="utf-8").splitlines()[-1])
    assert record["raw_response_text"] == "not valid json"
    assert record["action_valid"] is False
