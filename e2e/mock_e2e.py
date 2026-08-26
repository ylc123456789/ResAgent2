"""Deterministic mock golden loop for Phase 4.

Runs the full closed loop with no real LLM, no real legacy modules and no
network:

    ResearchRequest -> PlanningPort -> code -> experiment -> scientific_analyze

The experiment asks the user a question, which pauses the run and is answered
programmatically to prove ask-user resume flows through orchestrator AND the
shared runtime (``parent_session_id`` is consumed by ``AgentLoop.run``).
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    ArtifactCandidate,
    Capability,
    ResearchRequest,
    RunBudget,
    RunStatus,
    UserAnswer,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSource,
)
from resagent2_orchestrator import (
    DeterministicPlanningPort,
    JsonRunStore,
    ModuleBinding,
    WorkflowScheduler,
)
from resagent2_runtime import (
    AgentAction,
    AgentDefinition,
    AgentLoop,
    AllowListPermissionPolicy,
    AskUserTool,
    CompletionDecision,
    ContextSection,
    FinishTool,
    InMemorySessionStore,
    ScriptedLLMClient,
)

RUN_ID = "run_golden"


class _GoldenCheck:
    """Deterministic finalizer: accept a finish candidate with a fixed payload."""

    def __init__(self, summary: str, payload: dict, artifacts: list[ArtifactCandidate]):
        self._summary = summary
        self._payload = payload
        self._artifacts = artifacts

    def evaluate(self, state, candidate) -> CompletionDecision:
        return CompletionDecision(
            complete=True,
            summary=self._summary,
            payload=self._payload,
            artifacts=self._artifacts,
        )


def _context(request, state) -> list[ContextSection]:
    return [
        ContextSection(name="task", content=f"Goal: {request.goal}", priority=100, required=True),
        ContextSection(name="memory", content=str(state.memory), priority=50),
    ]


class RuntimeAgentAdapter:
    """A ModulePort that drives the shared runtime AgentLoop deterministically."""

    def __init__(self, definition: AgentDefinition, loop: AgentLoop) -> None:
        self._definition = definition
        self._loop = loop

    def invoke(self, request):
        session_id = request.parent_session_id or (
            f"session_{request.task_id}_{request.attempt_number}"
        )
        return self._loop.run(self._definition, request, session_id=session_id)


def _definition(
    name: str,
    owner: AgentOwner,
    actions: list[AgentAction],
    check: _GoldenCheck,
    *,
    tools: tuple = (FinishTool(),),
) -> AgentDefinition:
    return AgentDefinition(
        name=name,
        owner=owner,
        system_prompt="Follow the typed task and use only the provided tools.",
        tools=tools,
        llm_client=ScriptedLLMClient(actions),
        context_builder=_context,
        permission_policy=AllowListPermissionPolicy({tool.name for tool in tools}),
        completion_check=check,
    )


def run_mock_e2e(*, workdir: Path | None = None) -> object:
    """Run the golden loop once and return the completed ResearchRun."""
    workdir = workdir or Path(tempfile.mkdtemp(prefix="resagent2-e2e-"))
    workspace = workdir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "method.py").write_text("# minimal method\n", encoding="utf-8")
    (workspace / "metrics.json").write_text('{"accuracy": 0.9}\n', encoding="utf-8")
    (workspace / "conclusion.json").write_text('{"verdict": "supports"}\n', encoding="utf-8")

    grant = WorkspaceGrant(
        root=str(workspace),
        mode=WorkspaceMode.READ_WRITE,
        allowed_paths=["."],
        source=WorkspaceSource.EXISTING,
    )
    loop = AgentLoop(store=InMemorySessionStore())

    code = RuntimeAgentAdapter(
        _definition(
            "code",
            AgentOwner.CODING,
            [AgentAction(tool="finish", arguments={"result": {"patch": "method.py"}})],
            _GoldenCheck(
                "code changed",
                {"changed_files": ["method.py"]},
                [
                    ArtifactCandidate(
                        kind="code_change",
                        path="method.py",
                        media_type="text/x-python",
                        summary="minimal patch",
                    )
                ],
            ),
        ),
        loop,
    )
    experiment = RuntimeAgentAdapter(
        _definition(
            "experiment",
            AgentOwner.EXPERIMENT,
            [
                AgentAction(
                    tool="ask_user",
                    arguments={
                        "text": "Which dataset should be used?",
                        "requested_fields": ["dataset"],
                        "reason": "No dataset was selected.",
                    },
                ),
                AgentAction(
                    tool="finish",
                    arguments={"result": {"metrics": {"accuracy": 0.9}}},
                ),
            ],
            _GoldenCheck(
                "experiment ran",
                {"metrics": {"accuracy": 0.9}},
                [
                    ArtifactCandidate(
                        kind="experiment_result",
                        path="metrics.json",
                        media_type="application/json",
                        summary="evaluation metrics",
                    )
                ],
            ),
            tools=(AskUserTool(), FinishTool()),
        ),
        loop,
    )
    analyze = RuntimeAgentAdapter(
        _definition(
            "analyze",
            AgentOwner.SCIENTIFIC,
            [AgentAction(tool="finish", arguments={"result": {"verdict": "supports"}})],
            _GoldenCheck(
                "analysis complete",
                {"conclusion": {"verdict": "supports"}},
                [
                    ArtifactCandidate(
                        kind="scientific_decision",
                        path="conclusion.json",
                        media_type="application/json",
                        summary="scientific conclusion",
                    )
                ],
            ),
        ),
        loop,
    )

    scheduler = WorkflowScheduler(
        bindings={
            Capability.CODE_MODIFY: ModuleBinding(
                owner=AgentOwner.CODING, port=code, workspace=grant
            ),
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT, port=experiment, workspace=grant
            ),
            Capability.SCIENTIFIC_ANALYZE: ModuleBinding(
                owner=AgentOwner.SCIENTIFIC, port=analyze, workspace=grant
            ),
        },
        store=JsonRunStore(workdir / "state"),
        artifact_root=workdir / "artifacts",
    )

    request = ResearchRequest(
        goal="Determine whether the method improves accuracy",
        budget=RunBudget(
            max_tasks=5,
            max_attempts_per_task=3,
            max_llm_calls=50,
            timeout_seconds=60,
        ),
    )
    proposal = DeterministicPlanningPort().propose(request)
    assert all(
        task.capability not in {Capability.SCIENTIFIC_PLAN, Capability.ASK_USER}
        for task in proposal.tasks
    ), "planning must not appear as a WorkflowTask"

    run = scheduler.create_run(RUN_ID, request, proposal)
    run = scheduler.run_until_stable(RUN_ID)
    if run.status == RunStatus.PAUSED:
        answer = UserAnswer(
            question_id=run.pending_question.id,
            values={"dataset": "demo"},
            answered_at=datetime.now(UTC),
        )
        run = scheduler.answer_question(RUN_ID, answer)
        run = scheduler.run_until_stable(RUN_ID)

    assert run.status == RunStatus.COMPLETED, f"golden loop did not complete: {run.status}"
    assert all(task.attempts for task in run.workflow.tasks), "every task needs an Attempt"
    assert len(run.artifacts) == 3, "every task must register an Artifact"

    experiment_task = next(
        task for task in run.workflow.tasks if task.capability == Capability.EXPERIMENT_RUN
    )
    assert experiment_task.attempts[0].session is not None
    assert experiment_task.attempts[1].session is not None
    assert (
        experiment_task.attempts[0].session.id == experiment_task.attempts[1].session.id
    ), "ask-user resume must reuse the same runtime session"
    assert any(attempt.payload is not None for attempt in experiment_task.attempts)

    # Prove recovery: a fresh store reads the same durable state back from disk.
    recovered = JsonRunStore(workdir / "state").load(RUN_ID)
    assert recovered.status == RunStatus.COMPLETED

    return recovered


def _summarize(run) -> str:
    lines = [f"run={run.run_id} status={run.status.value} artifacts={len(run.artifacts)}"]
    for task in run.workflow.tasks:
        attempts = ", ".join(f"{a.number}:{a.status.value}" for a in task.attempts)
        lines.append(f"  {task.id} [{task.capability.value}] attempts={attempts}")
    return "\n".join(lines)


def main() -> None:
    run = run_mock_e2e()
    print(_summarize(run))


if __name__ == "__main__":
    main()
