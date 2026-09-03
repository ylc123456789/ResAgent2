"""Cross-layer recovery: client bad-JSON exhausts retries, loop returns retryable.

The pieces (client bounded retry, loop TOOL_FAILED, scheduler attempt retry) are
each tested in isolation elsewhere; this test verifies the two runtime links in
the chain — the client and the loop — do not break between them.
"""

import json
from datetime import UTC, datetime
from unittest import mock

from resagent2_contracts import (
    AgentOwner,
    Capability,
    CodeUnderstandInput,
    ErrorCode,
    ModuleStatus,
    ModuleTaskRequest,
    ResearchRequest,
    RunBudget,
    RunStatus,
    TaskProposal,
    TaskStatus,
    TaskBudget,
    WorkflowProposal,
)
from resagent2_orchestrator import (
    InMemoryRunStore,
    ModuleBinding,
    ResearchRun,
    WorkflowScheduler,
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


class _LoopPort:
    def __init__(self, definition: AgentDefinition) -> None:
        self.definition = definition
        self.loop = AgentLoop(store=InMemorySessionStore())
        self.requests: list[ModuleTaskRequest] = []

    def invoke(self, request: ModuleTaskRequest):
        self.requests.append(request)
        return self.loop.run(
            self.definition, request, session_id=f"session_{request.attempt_number}"
        )


def test_bad_json_recovers_through_scheduler_attempt_retry(monkeypatch, tmp_path) -> None:
    """A client failure becomes one failed attempt, then a normal retry succeeds."""
    monkeypatch.setenv("TEST_LLM_KEY", "dummy")
    client = OpenAICompatibleClient(
        model="test-model",
        api_base="https://example.com/v1",
        api_key_env="TEST_LLM_KEY",
        trace_dir=tmp_path / "traces",
        trace_level="full",
    )
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
    port = _LoopPort(definition)
    store = InMemoryRunStore()
    engine = WorkflowScheduler(
        bindings={
            Capability.CODE_UNDERSTAND: ModuleBinding(
                owner=AgentOwner.CODING,
                port=port,
            )
        },
        store=store,
        data_root=tmp_path / "data",
    )
    request = ResearchRequest(
        goal="exercise retry recovery",
        budget=RunBudget(
            max_tasks=1,
            max_attempts_per_task=2,
            max_llm_calls=10,
            timeout_seconds=60,
        ),
    )
    now = datetime.now(UTC)
    store.save(
        ResearchRun(
            run_id="run_recovery_chain",
            request=request,
            status=RunStatus.RUNNING,
            created_at=now,
            updated_at=now,
        )
    )
    engine.accept_proposal(
        "run_recovery_chain",
        WorkflowProposal(
            work_request_id="work_legacy_initial",
            summary="recover after malformed provider content",
            compilation_rationale="exercise the bounded retry chain",
            tasks=[
                TaskProposal(
                    id="task_recovery",
                    work_request_id="work_legacy_initial",
                    capability=Capability.CODE_UNDERSTAND,
                    goal="Finish after one retryable provider failure",
                    inputs=CodeUnderstandInput(question="q"),
                )
            ],
        ),
    )
    bad = _FakeResponse({"choices": [{"message": {"content": "not valid json"}}]})
    valid = _FakeResponse(
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"tool": "finish", "arguments": {"result": {}}}
                        )
                    }
                }
            ]
        }
    )

    with (
        mock.patch("resagent2_runtime.llm.time.sleep"),
        mock.patch(
            "resagent2_runtime.llm.urlopen",
            side_effect=[bad, bad, bad, valid],
        ),
    ):
        run = engine.run_until_stable("run_recovery_chain")

    workflow_task = run.workflow.tasks[0]
    assert workflow_task.status == TaskStatus.COMPLETED
    assert [item.status.value for item in workflow_task.attempts] == [
        "failed",
        "completed",
    ]
    assert [item.attempt_number for item in port.requests] == [1, 2]
    assert run.llm_calls_used == 4

    records = [
        json.loads(line)
        for line in (tmp_path / "traces" / "llm_traces.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [record["action_valid"] for record in records] == [False, True]
    assert records[0]["raw_response_text"] == "not valid json"
    assert records[1]["parsed_action"]["tool"] == "finish"
