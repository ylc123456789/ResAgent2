"""Local ScriptedLLM validation of the Phase 7 real E2E scenario acceptance.

Drives the deterministic scenarios (1: direct inconclusive, 4: ask/resume)
through the real ResearchController with a scripted Scientific Agent, then
checks the same acceptance predicates used by ``e2e/real_e2e.py``.
"""

from datetime import UTC, datetime
from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    Capability,
    CapabilityDefinition,
    CapabilityRegistry,
    ResearchRequest,
    RunBudget,
    ScientificVerdict,
    UserAnswer,
    WorkflowProposal,
)
from resagent2_orchestrator import (
    DeterministicWorkflowCompiler,
    JsonRunStore,
    ResearchController,
    WorkflowScheduler,
)
from resagent2_runtime import JsonSessionStore, ScriptedLLMClient
from resagent2_scientific import ScientificAgent

from e2e.real_e2e import (
    _ask_resume_succeeded,
    _ask_start_succeeded,
    _direct_succeeded,
)


def _registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        definitions=[
            CapabilityDefinition(
                capability=Capability.CODE_MODIFY,
                owner=AgentOwner.CODING,
                request_model="CodeModifyInput",
                result_model="CodeModifyResult",
                permission_policy="read_write_workspace",
                completion_evidence=["code_change"],
            ),
            CapabilityDefinition(
                capability=Capability.EXPERIMENT_RUN,
                owner=AgentOwner.EXPERIMENT,
                request_model="ExperimentRunInput",
                result_model="ExperimentResult",
                permission_policy="read_write_workspace",
                completion_evidence=["experiment_result"],
            ),
        ]
    )


def _controller(workdir: Path, scientific: ScientificAgent) -> ResearchController:
    scheduler = WorkflowScheduler(
        bindings={},
        store=JsonRunStore(workdir / "state"),
        artifact_root=workdir / "artifacts",
    )
    return ResearchController(
        scientific_port=scientific,
        compiler=DeterministicWorkflowCompiler(
            WorkflowProposal(
                work_request_id="work_x",
                summary="no work",
                compilation_rationale="no work",
                tasks=[],
            ),
            patch=None,
        ),
        scheduler=scheduler,
        registry=_registry(),
    )


def _finish_inconclusive() -> dict:
    return {
        "tool": "finish",
        "arguments": {
            "opinion": {"verdict": ScientificVerdict.INCONCLUSIVE.value, "statement": "insufficient evidence"},
            "summary": "done",
        },
    }


def _ask_user() -> dict:
    return {
        "tool": "ask_user",
        "arguments": {
            "assessment": {"statement": "need clarification"},
            "text": "Which metric should be reported?",
            "requested_fields": ["metric"],
            "reason": "ambiguous goal",
        },
    }


def test_direct_inconclusive(tmp_path) -> None:
    scientific = ScientificAgent(
        ScriptedLLMClient([_finish_inconclusive()]),
        store=JsonSessionStore(tmp_path / "sci"),
    )
    controller = _controller(tmp_path, scientific)
    request = ResearchRequest(
        goal="Is the improvement causal or correlational?",
        constraints=["Do not request experiments or additional work."],
        budget=RunBudget(max_tasks=1, max_attempts_per_task=1, max_llm_calls=20, timeout_seconds=60),
    )

    run = controller.create_run("run_direct", request)

    assert _direct_succeeded(run)


def test_ask_start_then_resume(tmp_path) -> None:
    # First process: the Scientific Agent asks a question and the run pauses.
    sci_dir = tmp_path / "sci"
    controller1 = _controller(
        tmp_path,
        ScientificAgent(ScriptedLLMClient([_ask_user()]), store=JsonSessionStore(sci_dir)),
    )
    request = ResearchRequest(
        goal="Compare two methods and report accuracy.",
        budget=RunBudget(max_tasks=2, max_attempts_per_task=2, max_llm_calls=20, timeout_seconds=60),
    )
    run = controller1.create_run("run_ask", request)

    assert _ask_start_succeeded(run)

    # Second process: a fresh controller and session store over the same dir.
    controller2 = _controller(
        tmp_path,
        ScientificAgent(ScriptedLLMClient([_finish_inconclusive()]), store=JsonSessionStore(sci_dir)),
    )
    answer = UserAnswer(
        question_id=run.pending_question.id,
        values={"metric": "top-1 accuracy"},
        answered_at=datetime.now(UTC),
    )
    resumed = controller2.answer_question("run_ask", answer)

    assert _ask_resume_succeeded(resumed)
