"""Actual command receipts remain machine-readable across completion outcomes."""

from datetime import UTC, datetime
import json

import pytest

from resagent2_contracts import AgentOwner, AgentRequest, TaskBudget, WorkspaceGrant, WorkspaceMode, WorkspaceSourceKind
from resagent2_components import WorkspaceBoundary, WorkspaceObserver
from resagent2_experiment.completion import ExperimentCompletionCheck
from resagent2_runtime import (
    AgentDefinition, AgentLoop, AgentState, AllowListPermissionPolicy,
    FinishCandidate, FinishTool, ScriptedLLMClient, ToolObservation,
)
from resagent2_runtime.models import RuntimeModel


class CommandInput(RuntimeModel):
    """Fixed command fixture."""


class Command:
    name = "run_command"
    input_model = CommandInput

    def __init__(self, exit_code):
        self.exit_code = exit_code

    def execute(self, state, arguments):
        return ToolObservation(
            summary="Real command result", ok=self.exit_code == 0,
            value={
                "command": "python train.py", "exit_code": self.exit_code, "timed_out": False,
                "stdout_path": "stdout.log", "stderr_path": "stderr.log", "duration_seconds": 0.1,
                "stderr_tail": "error" if self.exit_code else "",
            },
        )


@pytest.mark.parametrize("exit_code", [0, 1])
def test_loop_returns_execution_record_for_success_and_failure(tmp_path, exit_code):
    boundary = WorkspaceBoundary(WorkspaceGrant(
        root=str(tmp_path), mode=WorkspaceMode.READ_WRITE, source=WorkspaceSourceKind.LOCAL,
    ))
    tools = (Command(exit_code), FinishTool())
    definition = AgentDefinition(
        name="experiment", owner=AgentOwner.EXPERIMENT, system_prompt="Run experiment",
        tools=tools, llm_client=ScriptedLLMClient([
            {"tool": "run_command", "arguments": {}},
            {"tool": "finish", "arguments": {"report": "Recorded the outcome"}},
        ]),
        context_builder=lambda *_: [],
        permission_policy=AllowListPermissionPolicy({tool.name for tool in tools}),
        completion_check=ExperimentCompletionCheck(WorkspaceObserver(boundary)),
    )
    result = AgentLoop().run(definition, AgentRequest(
        run_id="run_records", task_id="task_records", attempt_number=1, agent=AgentOwner.EXPERIMENT,
        instruction="Run", budget=TaskBudget(max_llm_calls=3, timeout_seconds=10),
    ), session_id="session_records")
    assert result.status == ("completed" if exit_code == 0 else "failed")
    record = json.loads(next(item.content for item in result.artifacts if item.kind == "execution_record"))
    assert record["results"][0]["exit_code"] == exit_code
    assert result.report == "Recorded the outcome"
    if exit_code:
        assert result.error.details["stderr_tail"] == "error"


@pytest.mark.parametrize("commands,failed_command", [
    ([("python train.py", 1, False), ("python train.py", 0, False)], None),
    ([("python train.py", 1, False), ('python  "train.py"', 0, False)], None),
    ([("python train.py", 1, False), ("python --version", 0, False)], "python train.py"),
    ([("python train.py", 0, True), ("python --version", 0, False)], "python train.py"),
    ([("python train.py", 1, False), ("python train.py --smoke", 0, False)], "python train.py"),
    ([("python train.py", 1, False), ("python other.py", 1, False),
      ("python train.py", 0, False)], "python other.py"),
])
def test_only_successful_retry_of_same_command_resolves_failure(tmp_path, commands, failed_command):
    from resagent2_runtime import AgentEvent
    now = datetime.now(UTC)
    state = AgentState(
        session_id="session_test", agent_name="experiment", owner=AgentOwner.EXPERIMENT,
        run_id="run_test", task_id="task_test", attempt_number=1, created_at=now, updated_at=now,
    )
    for sequence, (command, exit_code, timed_out) in enumerate(commands, start=1):
        observation = Command(exit_code).execute(state, CommandInput())
        observation.value.update(command=command, timed_out=timed_out,
                                 stdout_path=f"{sequence}.stdout", stderr_path=f"{sequence}.stderr")
        state.events.append(AgentEvent(
            sequence=sequence, step=sequence, type="observation", tool="run_command",
            data=observation.model_dump(mode="json"), created_at=now,
        ))
    boundary = WorkspaceBoundary(WorkspaceGrant(
        root=str(tmp_path), mode=WorkspaceMode.READ_ONLY, source=WorkspaceSourceKind.LOCAL,
    ))
    decision = ExperimentCompletionCheck(WorkspaceObserver(boundary)).evaluate(
        state, FinishCandidate(report="Recorded all outcomes"))
    assert decision.complete == (failed_command is None)
    if failed_command is not None:
        assert decision.failure.details["command"] == failed_command
        if not decision.failure.details["timed_out"]:
            assert decision.failure.details["stderr_tail"] == "error"
    record = json.loads(next(item.content for item in decision.artifacts if item.kind == "execution_record"))
    assert [(row["command"], row["exit_code"], row["timed_out"])
            for row in record["results"]] == commands
