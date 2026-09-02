"""Phase 7.6 scientific completion gate and final-report tests."""

from datetime import UTC, datetime

import pytest

from resagent2_contracts import (
    AgentOwner,
    ArtifactCandidate,
    ArtifactRef,
    Attempt,
    AttemptStatus,
    Capability,
    CapabilityDefinition,
    CapabilityRegistry,
    ErrorCode,
    ExperimentRunInput,
    ModuleError,
    ResearchRequest,
    RunBudget,
    RunStatus,
    ScientificCompletedResult,
    ScientificOpinion,
    ScientificVerdict,
    SessionRef,
    SessionStatus,
    TaskStatus,
    Workflow,
    WorkflowTask,
    WorkRequest,
    WorkRequestDraft,
    WorkRequestStatus,
)
from resagent2_orchestrator import (
    ArtifactRegistry,
    CompletionViolationCode,
    FinalReportRenderer,
    ResearchRun,
    ScientificCompletionValidator,
)

NOW = datetime(2026, 8, 28, tzinfo=UTC)


def registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        definitions=[
            CapabilityDefinition(
                capability=Capability.EXPERIMENT_RUN,
                owner=AgentOwner.EXPERIMENT,
                request_model="ExperimentRunInput",
                result_model="ExperimentResult",
                permission_policy="read_write_workspace",
                completion_evidence=["experiment_result"],
            )
        ]
    )


def session(status: SessionStatus = SessionStatus.COMPLETED) -> SessionRef:
    return SessionRef(
        id="session_scientific",
        module=AgentOwner.SCIENTIFIC,
        state_uri="memory://session_scientific",
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def evidence(*, run_id: str = "run_gate") -> ArtifactRef:
    return ArtifactRef(
        id="artifact_evidence",
        kind="literature_search",
        producer=AgentOwner.SCIENTIFIC,
        run_id=run_id,
        session_id="session_scientific",
        uri="file:///tmp/artifact_evidence.json",
        sha256="a" * 64,
        media_type="application/json",
        summary="registered evidence",
    )


def run_state(
    *,
    artifacts: dict[str, ArtifactRef] | None = None,
    observed: list[str] | None = None,
    workflow: Workflow | None = None,
    work_requests: list[WorkRequest] | None = None,
) -> ResearchRun:
    return ResearchRun(
        run_id="run_gate",
        request=ResearchRequest(
            goal="Evaluate the method",
            budget=RunBudget(
                max_tasks=5,
                max_attempts_per_task=2,
                max_llm_calls=20,
                timeout_seconds=60,
            ),
        ),
        status=RunStatus.RUNNING,
        workflow=workflow,
        artifacts=artifacts or {},
        scientific_observed_artifact_ids=observed or [],
        work_requests=work_requests or [],
        created_at=NOW,
        updated_at=NOW,
    )


def result(
    verdict: ScientificVerdict,
    *,
    evidence_ids: list[str] | None = None,
    observed: list[str] | None = None,
    limitations: list[str] | None = None,
) -> ScientificCompletedResult:
    return ScientificCompletedResult(
        status="completed",
        opinion=ScientificOpinion(
            verdict=verdict,
            statement="The evidence supports this judgment.",
            evidence_artifact_ids=evidence_ids or [],
            limitations=limitations or [],
        ),
        session=session(),
        observed_artifact_ids=observed or [],
        llm_calls=1,
    )


@pytest.mark.parametrize(
    "verdict",
    [
        ScientificVerdict.SUPPORTS,
        ScientificVerdict.REFUTES,
        ScientificVerdict.INCONCLUSIVE,
        ScientificVerdict.NOT_APPLICABLE,
    ],
)
def test_all_verdicts_pass_with_valid_combinations(verdict) -> None:
    needs_evidence = verdict in {ScientificVerdict.SUPPORTS, ScientificVerdict.REFUTES}
    artifact = evidence()
    ids = [artifact.id] if needs_evidence else []
    run = run_state(
        artifacts={artifact.id: artifact} if needs_evidence else {},
        observed=ids,
    )

    validation = ScientificCompletionValidator(registry()).validate(
        run,
        result(verdict, evidence_ids=ids, observed=ids),
    )

    assert validation.ok
    assert validation.report is not None
    assert [item.id for item in validation.report.evidence] == ids


def test_unknown_and_cross_run_evidence_are_rejected() -> None:
    artifact = evidence(run_id="run_other")
    run = run_state(artifacts={artifact.id: artifact}, observed=[artifact.id])

    validation = ScientificCompletionValidator(registry()).validate(
        run,
        result(
            ScientificVerdict.SUPPORTS,
            evidence_ids=[artifact.id],
            observed=[artifact.id],
        ),
    )

    assert not validation.ok
    assert CompletionViolationCode.UNKNOWN_EVIDENCE in {
        item.code for item in validation.violations
    }


def test_evidence_must_be_in_both_observed_traces() -> None:
    artifact = evidence()
    run = run_state(artifacts={artifact.id: artifact}, observed=[artifact.id])

    validation = ScientificCompletionValidator(registry()).validate(
        run,
        result(ScientificVerdict.SUPPORTS, evidence_ids=[artifact.id]),
    )

    assert CompletionViolationCode.UNOBSERVED_EVIDENCE in {
        item.code for item in validation.violations
    }


def test_forged_observed_trace_is_rejected() -> None:
    validation = ScientificCompletionValidator(registry()).validate(
        run_state(),
        result(ScientificVerdict.INCONCLUSIVE, observed=["artifact_forged"]),
    )

    assert CompletionViolationCode.UNKNOWN_EVIDENCE in {
        item.code for item in validation.violations
    }


def test_active_work_request_is_rejected() -> None:
    active = WorkRequest(
        id="work_1",
        run_id="run_gate",
        scientific_session_id="session_scientific",
        request=WorkRequestDraft(objective="Run an experiment", expected_evidence=["metric"]),
        status=WorkRequestStatus.REQUESTED,
        created_at=NOW,
        updated_at=NOW,
    )

    validation = ScientificCompletionValidator(registry()).validate(
        run_state(work_requests=[active]),
        result(ScientificVerdict.INCONCLUSIVE),
    )

    assert CompletionViolationCode.ACTIVE_CONTROL_STATE in {
        item.code for item in validation.violations
    }


def workflow_task(status: TaskStatus, attempts: list[Attempt]) -> Workflow:
    return Workflow(
        run_id="run_gate",
        revision=1,
        created_from="work_1",
        tasks=[
            WorkflowTask(
                id="task_experiment",
                work_request_id="work_1",
                capability=Capability.EXPERIMENT_RUN,
                goal="Run the experiment",
                inputs=ExperimentRunInput(instructions="Run once"),
                status=status,
                attempts=attempts,
            )
        ],
    )


@pytest.mark.parametrize(
    ("status", "attempts"),
    [
        (TaskStatus.PENDING, []),
        (
            TaskStatus.RUNNING,
            [
                Attempt(
                    number=1,
                    status=AttemptStatus.RUNNING,
                    started_at=NOW,
                )
            ],
        ),
        (
            TaskStatus.NEEDS_USER_INPUT,
            [
                Attempt(
                    number=1,
                    status=AttemptStatus.NEEDS_USER_INPUT,
                    started_at=NOW,
                )
            ],
        ),
    ],
)
def test_nonterminal_task_prevents_final_completion(status, attempts) -> None:
    validation = ScientificCompletionValidator(registry()).validate(
        run_state(workflow=workflow_task(status, attempts)),
        result(ScientificVerdict.INCONCLUSIVE),
    )

    assert CompletionViolationCode.ACTIVE_CONTROL_STATE in {
        item.code for item in validation.violations
    }


def test_failed_task_without_limitation_is_rejected() -> None:
    error = ModuleError(
        code=ErrorCode.TOOL_FAILED,
        message="experiment failed",
        retryable=False,
    )
    failed = Attempt(
        number=1,
        status=AttemptStatus.FAILED,
        started_at=NOW,
        finished_at=NOW,
        error=error,
    )

    validation = ScientificCompletionValidator(registry()).validate(
        run_state(workflow=workflow_task(TaskStatus.FAILED, [failed])),
        result(ScientificVerdict.INCONCLUSIVE),
    )

    codes = {item.code for item in validation.violations}
    assert CompletionViolationCode.MISSING_LIMITATIONS in codes


def test_failed_task_is_reported_without_agent_task_id() -> None:
    error = ModuleError(
        code=ErrorCode.TOOL_FAILED,
        message="experiment failed",
        retryable=False,
    )
    failed = Attempt(
        number=1,
        status=AttemptStatus.FAILED,
        started_at=NOW,
        finished_at=NOW,
        error=error,
    )

    validation = ScientificCompletionValidator(registry()).validate(
        run_state(workflow=workflow_task(TaskStatus.FAILED, [failed])),
        result(
            ScientificVerdict.INCONCLUSIVE,
            limitations=["The failed experiment leaves the comparison incomplete."],
        ),
    )

    assert validation.ok
    assert validation.report is not None
    assert [issue.task_id for issue in validation.report.execution_issues] == [
        "task_experiment"
    ]


def test_completed_task_requires_valid_terminal_attempt() -> None:
    validation = ScientificCompletionValidator(registry()).validate(
        run_state(workflow=workflow_task(TaskStatus.COMPLETED, [])),
        result(ScientificVerdict.INCONCLUSIVE),
    )

    assert CompletionViolationCode.INCONSISTENT_TASK_RESULT in {
        item.code for item in validation.violations
    }


def test_completed_task_rejects_artifact_from_wrong_owner() -> None:
    artifact = ArtifactRef(
        id="artifact_task_output",
        kind="experiment_result",
        producer=AgentOwner.SCIENTIFIC,
        run_id="run_gate",
        task_id="task_experiment",
        attempt_number=1,
        uri="file:///tmp/artifact_task_output.json",
        sha256="b" * 64,
        media_type="application/json",
        summary="wrong producer",
    )
    completed = Attempt(
        number=1,
        status=AttemptStatus.COMPLETED,
        started_at=NOW,
        finished_at=NOW,
        artifact_ids=[artifact.id],
    )

    validation = ScientificCompletionValidator(registry()).validate(
        run_state(
            artifacts={artifact.id: artifact},
            workflow=workflow_task(TaskStatus.COMPLETED, [completed]),
        ),
        result(ScientificVerdict.INCONCLUSIVE),
    )

    assert CompletionViolationCode.INCONSISTENT_TASK_RESULT in {
        item.code for item in validation.violations
    }


def test_renderer_is_deterministic_and_only_uses_typed_data() -> None:
    artifact = evidence()
    validation = ScientificCompletionValidator(registry()).validate(
        run_state(artifacts={artifact.id: artifact}, observed=[artifact.id]),
        result(
            ScientificVerdict.SUPPORTS,
            evidence_ids=[artifact.id],
            observed=[artifact.id],
        ),
    )
    assert validation.report is not None
    renderer = FinalReportRenderer()

    first = renderer.render(validation.report)
    second = renderer.render(validation.report)

    assert first == second
    assert first.candidate.kind == "final_report"
    assert first.candidate.metadata == {"source_type": "final_report"}
    assert artifact.sha256 in first.content


def test_final_report_registration_is_idempotent_after_file_write(tmp_path) -> None:
    validation = ScientificCompletionValidator(registry()).validate(
        run_state(),
        result(ScientificVerdict.INCONCLUSIVE),
    )
    assert validation.report is not None
    rendered = FinalReportRenderer().render(validation.report)
    artifact_registry = ArtifactRegistry(tmp_path / "artifacts")

    first = artifact_registry.register_final_report(
        rendered.candidate,
        rendered.content,
        run_id="run_gate",
    )
    retried = artifact_registry.register_final_report(
        rendered.candidate,
        rendered.content,
        run_id="run_gate",
    )

    assert retried == first
    assert len(first.sha256) == 64


def test_register_scientific_freezes_with_session_provenance(tmp_path) -> None:
    artifact_registry = ArtifactRegistry(tmp_path / "artifacts")
    candidate = ArtifactCandidate(
        kind="literature_search",
        path="literature_search.json",
        media_type="application/json",
        summary="literature results",
        metadata={"papers": [{"paper_id": "2301.00001", "title": "T"}]},
    )

    first = artifact_registry.register_scientific(
        candidate, run_id="run_gate", session_id="session_scientific"
    )

    assert first.producer == AgentOwner.SCIENTIFIC
    assert first.session_id == "session_scientific"
    assert first.task_id is None
    assert first.attempt_number is None
    assert first.kind == "literature_search"
    assert len(first.sha256) == 64

    # Same content -> same id and file, idempotent.
    retried = artifact_registry.register_scientific(
        candidate, run_id="run_gate", session_id="session_scientific"
    )
    assert retried == first
