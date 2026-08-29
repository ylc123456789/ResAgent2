"""Tests for the native Scientific Agent (DEVELOPMENT_PLAN §7.4)."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from resagent2_contracts import (
    AgentOwner,
    ArtifactRef,
    ErrorCode,
    ModuleError,
    ResearchRequest,
    RunBudget,
    ScientificOpinion,
    ScientificTurnRequest,
    ScientificVerdict,
    TaskBudget,
    WorkOutcome,
    WorkRequestDraft,
    WorkTaskOutcome,
)
from resagent2_scientific import ScientificAgent
from resagent2_runtime import ScriptedLLMClient

NOW = datetime(2026, 8, 28, tzinfo=UTC)


def research_request() -> ResearchRequest:
    return ResearchRequest(
        goal="Evaluate the method",
        budget=RunBudget(
            max_tasks=5,
            max_attempts_per_task=2,
            max_llm_calls=20,
            timeout_seconds=60,
        ),
    )


def turn(*, work_outcome=None, unresolved=(), parent=None, artifacts=()) -> ScientificTurnRequest:
    previous = None
    if work_outcome is not None:
        previous = WorkRequestDraft(
            objective="Produce evidence", expected_evidence=["metric"]
        )
    return ScientificTurnRequest(
        run_id="run_example",
        research=research_request(),
        authorized_artifacts=list(artifacts),
        work_outcome=work_outcome,
        previous_work_request=previous,
        unresolved_task_outcomes=list(unresolved),
        budget=TaskBudget(max_steps=10, max_llm_calls=10, timeout_seconds=60),
        parent_session_id=parent,
    )


def test_work_outcome_requires_previous_work_request() -> None:
    outcome = WorkOutcome(
        work_request_id="work_1",
        workflow_revision=1,
        summary="ran",
        tasks=[WorkTaskOutcome(task_id="task_x", status="completed", summary="ran")],
    )
    with pytest.raises(ValidationError, match="paired"):
        ScientificTurnRequest(
            run_id="run_example",
            research=research_request(),
            work_outcome=outcome,
            budget=TaskBudget(max_steps=10, max_llm_calls=10, timeout_seconds=60),
            parent_session_id="session_x",
        )


def artifact(artifact_id: str, tmp_path: Path) -> ArtifactRef:
    content = b'{"value": 1}'
    path = tmp_path / f"{artifact_id}.json"
    path.write_bytes(content)
    return ArtifactRef(
        id=artifact_id,
        kind="experiment_result",
        producer=AgentOwner.EXPERIMENT,
        run_id="run_example",
        task_id="task_experiment",
        attempt_number=1,
        uri=path.as_uri(),
        sha256=hashlib.sha256(content).hexdigest(),
        media_type="application/json",
        summary="evidence",
    )


def opinion(verdict=ScientificVerdict.INCONCLUSIVE, evidence=()) -> dict:
    return {
        "verdict": verdict.value,
        "statement": "A statement",
        "evidence_artifact_ids": list(evidence),
    }


def test_finish_with_existing_evidence_completes(tmp_path: Path) -> None:
    agent = ScientificAgent(
        ScriptedLLMClient(
            [
                {"tool": "read_artifact", "arguments": {"artifact_id": "artifact_1"}},
                {
                    "tool": "finish",
                    "arguments": {
                        "opinion": opinion(
                            ScientificVerdict.SUPPORTS, evidence=["artifact_1"]
                        ),
                        "summary": "supported",
                    },
                },
            ]
        )
    )
    result = agent.run(turn(artifacts=[artifact("artifact_1", tmp_path)]))

    assert result.status == "completed"
    assert result.opinion.verdict == ScientificVerdict.SUPPORTS
    assert result.observed_artifact_ids == ["artifact_1"]


def test_request_work_pauses_with_assessment_and_draft() -> None:
    agent = ScientificAgent(
        ScriptedLLMClient(
            [
                {
                    "tool": "request_work",
                    "arguments": {
                        "assessment": {"statement": "need more evidence"},
                        "work_request": {
                            "objective": "Run the experiment",
                            "expected_evidence": ["accuracy"],
                        },
                    },
                }
            ]
        )
    )
    result = agent.run(turn())

    assert result.status == "request_work"
    assert result.assessment.statement == "need more evidence"
    assert result.work_request.expected_evidence == ["accuracy"]


def test_finish_after_search_tracks_observed_artifact(tmp_path: Path) -> None:
    from resagent2_capabilities import LiteraturePaper

    class _Backend:
        def search(self, query, *, max_results, start_year=None, end_year=None):
            return [
                LiteraturePaper(
                    paper_id="2301.00001",
                    title="T",
                    authors=["A"],
                    abstract="abs",
                    source_url="https://arxiv.org/abs/2301.00001",
                )
            ]

    class _Register:
        def register_scientific(self, candidate, *, run_id, session_id):
            content = b"{}"
            path = tmp_path / "lit.json"
            path.write_bytes(content)
            return ArtifactRef(
                id="artifact_lit",
                kind="literature_search",
                producer=AgentOwner.SCIENTIFIC,
                run_id=run_id,
                session_id=session_id,
                uri=path.as_uri(),
                sha256=hashlib.sha256(content).hexdigest(),
                media_type="application/json",
                summary="literature",
            )

    agent = ScientificAgent(
        ScriptedLLMClient(
            [
                {
                    "tool": "literature_search",
                    "arguments": {"query": "graph networks", "max_results": 5},
                },
                {
                    "tool": "finish",
                    "arguments": {
                        "opinion": opinion(
                            ScientificVerdict.INCONCLUSIVE,
                            evidence=["artifact_lit"],
                        ),
                        "summary": "reviewed literature",
                    },
                },
            ]
        ),
        literature_backend=_Backend(),
        registration_port=_Register(),
    )
    result = agent.run(turn())

    assert result.status == "completed"
    assert result.observed_artifact_ids == ["artifact_lit"]


def test_ask_user_pauses_with_question_and_assessment() -> None:
    agent = ScientificAgent(
        ScriptedLLMClient(
            [
                {
                    "tool": "ask_user",
                    "arguments": {
                        "assessment": {"statement": "need dataset choice"},
                        "text": "Which dataset?",
                        "requested_fields": ["dataset"],
                        "reason": "no dataset selected",
                    },
                }
            ]
        )
    )
    result = agent.run(turn())

    assert result.status == "needs_user_input"
    assert result.question.text == "Which dataset?"
    assert result.assessment.statement == "need dataset choice"


def test_resume_with_work_outcome_reuses_session() -> None:
    agent = ScientificAgent(
        ScriptedLLMClient(
            [
                {"tool": "request_work", "arguments": {
                    "assessment": {"statement": "need evidence"},
                    "work_request": {
                        "objective": "Run experiment",
                        "expected_evidence": ["accuracy"],
                    },
                }},
                {"tool": "finish", "arguments": {
                    "opinion": opinion(ScientificVerdict.INCONCLUSIVE),
                    "summary": "done",
                }},
            ]
        )
    )
    first = agent.run(turn())
    assert first.status == "request_work"
    session_id = first.session.id

    outcome = WorkOutcome(
        work_request_id="work_round1",
        workflow_revision=1,
        summary="ran experiment",
        tasks=[
            WorkTaskOutcome(
                task_id="task_experiment", status="completed", summary="ran"
            )
        ],
    )
    second = agent.run(turn(work_outcome=outcome, parent=session_id))

    assert second.status == "completed"
    assert second.session.id == session_id


def test_ask_user_resume_reuses_session() -> None:
    agent = ScientificAgent(
        ScriptedLLMClient(
            [
                {"tool": "ask_user", "arguments": {
                    "assessment": {"statement": "need input"},
                    "text": "Which?",
                    "requested_fields": ["x"],
                    "reason": "need",
                }},
                {"tool": "finish", "arguments": {
                    "opinion": opinion(ScientificVerdict.INCONCLUSIVE),
                    "summary": "done",
                }},
            ]
        )
    )
    first = agent.run(turn())
    assert first.status == "needs_user_input"

    second = agent.run(turn(parent=first.session.id))
    assert second.status == "completed"
    assert second.session.id == first.session.id


def test_unobserved_evidence_is_rejected(tmp_path: Path) -> None:
    agent = ScientificAgent(
        ScriptedLLMClient(
            [
                {
                    "tool": "finish",
                    "arguments": {
                        "opinion": opinion(
                            ScientificVerdict.SUPPORTS, evidence=["artifact_fake"]
                        ),
                        "summary": "fabricated",
                    },
                }
            ]
        )
    )
    result = agent.run(turn(artifacts=[artifact("artifact_1", tmp_path)]))

    assert result.status == "failed"
    assert result.error.code == ErrorCode.TOOL_FAILED


def test_unacknowledged_task_is_rejected() -> None:
    unresolved = [
        WorkTaskOutcome(
            task_id="task_experiment",
            status="failed",
            summary="crashed",
            error=ModuleError(
                code=ErrorCode.TOOL_FAILED, message="crashed", retryable=False
            ),
        )
    ]
    agent = ScientificAgent(
        ScriptedLLMClient(
            [
                {
                    "tool": "finish",
                    "arguments": {
                        "opinion": opinion(ScientificVerdict.INCONCLUSIVE),
                        "summary": "ignored failure",
                    },
                }
            ]
        )
    )
    result = agent.run(turn(unresolved=unresolved))

    assert result.status == "failed"
    assert result.error.code == ErrorCode.TOOL_FAILED


def test_budget_exhaustion_returns_failed(tmp_path: Path) -> None:
    # A scripted client that keeps reading the same artifact past the LLM budget.
    actions = [
        {
            "tool": "read_artifact",
            "arguments": {"artifact_id": "artifact_1"},
        }
    ] * 20
    agent = ScientificAgent(ScriptedLLMClient(actions))
    result = agent.run(turn(artifacts=[artifact("artifact_1", tmp_path)]))

    assert result.status == "failed"
    assert result.error.code == ErrorCode.BUDGET_EXHAUSTED


def test_request_work_assessment_cannot_cite_unobserved_evidence() -> None:
    # Unobserved evidence is now a recoverable tool-level rejection (ok=False),
    # not a hard post-loop contract error. With no corrective action left, the
    # scripted client exhausts.
    agent = ScientificAgent(
        ScriptedLLMClient(
            [
                {
                    "tool": "request_work",
                    "arguments": {
                        "assessment": {
                            "statement": "need more evidence",
                            "evidence_artifact_ids": ["artifact_fake"],
                        },
                        "work_request": {
                            "objective": "Run experiment",
                            "expected_evidence": ["accuracy"],
                        },
                    },
                }
            ]
        )
    )
    result = agent.run(turn())

    assert result.status == "failed"
    assert result.error.code == ErrorCode.TOOL_FAILED


def test_unobserved_evidence_can_be_recovered_by_reading(tmp_path) -> None:
    artifact_ref = artifact("artifact_1", tmp_path)
    request = {
        "tool": "request_work",
        "arguments": {
            "assessment": {
                "statement": "need more evidence",
                "evidence_artifact_ids": ["artifact_1"],
            },
            "work_request": {
                "objective": "Run experiment",
                "expected_evidence": ["accuracy"],
            },
        },
    }
    agent = ScientificAgent(
        ScriptedLLMClient(
            [
                request,
                {"tool": "read_artifact", "arguments": {"artifact_id": "artifact_1"}},
                request,
            ]
        )
    )
    result = agent.run(turn(artifacts=[artifact_ref]))

    assert result.status == "request_work"


def test_unknown_acknowledged_task_id_is_rejected() -> None:
    agent = ScientificAgent(
        ScriptedLLMClient(
            [
                {
                    "tool": "finish",
                    "arguments": {
                        "opinion": {
                            **opinion(ScientificVerdict.INCONCLUSIVE),
                            "acknowledged_task_ids": ["task_unknown"],
                            "limitations": ["something failed"],
                        },
                        "summary": "acknowledged a ghost task",
                    },
                }
            ]
        )
    )
    result = agent.run(turn())

    assert result.status == "failed"
    assert result.error.code == ErrorCode.TOOL_FAILED


def test_repeated_first_request_is_idempotent(tmp_path: Path) -> None:
    agent = ScientificAgent(
        ScriptedLLMClient(
            [
                {
                    "tool": "request_work",
                    "arguments": {
                        "assessment": {"statement": "need evidence"},
                        "work_request": {
                            "objective": "Run experiment",
                            "expected_evidence": ["accuracy"],
                        },
                    },
                },
                # A second action must never be consumed: the repeated request
                # is answered from the persisted result.
                {
                    "tool": "finish",
                    "arguments": {
                        "opinion": opinion(ScientificVerdict.INCONCLUSIVE),
                        "summary": "should not run",
                    },
                },
            ]
        )
    )
    request = turn()
    first = agent.run(request)
    assert first.status == "request_work"

    duplicate = agent.run(request)
    assert duplicate.status == "request_work"
    assert duplicate.work_request.expected_evidence == ["accuracy"]


def test_context_preserves_earlier_read_evidence(tmp_path: Path) -> None:
    """Reading A then B must keep both summaries in the session memory."""
    agent = ScientificAgent(
        ScriptedLLMClient(
            [
                {"tool": "read_artifact", "arguments": {"artifact_id": "artifact_a"}},
                {"tool": "read_artifact", "arguments": {"artifact_id": "artifact_b"}},
                {
                    "tool": "finish",
                    "arguments": {
                        "opinion": opinion(
                            ScientificVerdict.INCONCLUSIVE,
                            evidence=["artifact_a", "artifact_b"],
                        ),
                        "summary": "two evidence files",
                    },
                },
            ]
        )
    )
    result = agent.run(
        turn(artifacts=[artifact("artifact_a", tmp_path), artifact("artifact_b", tmp_path)])
    )

    assert result.status == "completed"
    state = agent.store.load(result.session.id)
    summaries = state.memory["read_artifact_summaries"]
    assert set(summaries) == {"artifact_a", "artifact_b"}


def test_repeated_work_outcome_delivery_is_idempotent() -> None:
    """Delivering the same work_outcome twice must return the same result,
    not consume a second LLM action."""
    agent = ScientificAgent(
        ScriptedLLMClient(
            [
                {"tool": "request_work", "arguments": {
                    "assessment": {"statement": "need evidence"},
                    "work_request": {
                        "objective": "Run experiment",
                        "expected_evidence": ["accuracy"],
                    },
                }},
                {"tool": "finish", "arguments": {
                    "opinion": opinion(ScientificVerdict.INCONCLUSIVE),
                    "summary": "done",
                }},
                # A third action that must never be reached on duplicate delivery.
                {"tool": "finish", "arguments": {
                    "opinion": opinion(ScientificVerdict.SUPPORTS, evidence=["artifact_x"]),
                    "summary": "should not run",
                }},
            ]
        )
    )
    first = agent.run(turn())
    assert first.status == "request_work"
    session_id = first.session.id

    outcome = WorkOutcome(
        work_request_id="work_round1",
        workflow_revision=1,
        summary="ran experiment",
        tasks=[
            WorkTaskOutcome(task_id="task_experiment", status="completed", summary="ran")
        ],
    )
    resume = turn(work_outcome=outcome, parent=session_id)
    result = agent.run(resume)
    assert result.status == "completed"

    duplicate = agent.run(resume)
    assert duplicate.status == "completed"
    assert duplicate.opinion.verdict == ScientificVerdict.INCONCLUSIVE
    assert duplicate.opinion == result.opinion
