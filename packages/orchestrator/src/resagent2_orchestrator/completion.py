"""Deterministic scientific completion validation and final-report rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from resagent2_contracts import (
    AgentOwner,
    ArtifactCandidate,
    ArtifactRef,
    AttemptStatus,
    CapabilityRegistry,
    RunId,
    ScientificCompletedResult,
    ScientificOpinion,
    SessionStatus,
    TaskStatus,
    WorkRequestStatus,
    WorkTaskOutcome,
)

from .models import (
    CompletionViolation,
    CompletionViolationCode,
    ResearchRun,
)


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _CompletionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FinalReportData(_CompletionModel):
    """Only facts the deterministic final-report renderer may consume."""

    run_id: RunId
    goal: NonEmptyText
    opinion: ScientificOpinion
    evidence: list[ArtifactRef]
    execution_issues: list[WorkTaskOutcome] = Field(default_factory=list)


@dataclass(frozen=True)
class CompletionValidation:
    """One validation result: either violations or typed report data."""

    violations: tuple[CompletionViolation, ...] = ()
    report: FinalReportData | None = None

    @property
    def ok(self) -> bool:
        return not self.violations and self.report is not None


@dataclass(frozen=True)
class RenderedFinalReport:
    """A validated candidate paired with its deterministic UTF-8 content."""

    candidate: ArtifactCandidate
    content: str


class ScientificCompletionValidator:
    """Validate closure consistency without judging scientific truth."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self._owners = {
            definition.capability: definition.owner
            for definition in registry.definitions
        }

    def validate(
        self,
        run: ResearchRun,
        result: ScientificCompletedResult,
    ) -> CompletionValidation:
        # Work on a validated deep copy so this pure validator cannot mutate the
        # controller's live object through nested Pydantic models.
        snapshot = ResearchRun.model_validate(run.model_dump())
        violations: list[CompletionViolation] = []

        self._validate_session(snapshot, result, violations)
        self._validate_control_state(snapshot, violations)
        self._validate_opinion(result.opinion, violations)
        evidence = self._validate_evidence(snapshot, result, violations)
        issues = self._validate_unresolved_tasks(snapshot, result.opinion, violations)
        self._validate_completed_tasks(snapshot, violations)
        self._validate_ids(snapshot, result, violations)

        if violations:
            return CompletionValidation(violations=tuple(violations))
        return CompletionValidation(
            report=FinalReportData(
                run_id=snapshot.run_id,
                goal=snapshot.request.goal,
                opinion=result.opinion,
                evidence=evidence,
                execution_issues=issues,
            )
        )

    @staticmethod
    def _add(
        violations: list[CompletionViolation],
        code: CompletionViolationCode,
        message: str,
        *related_ids: str,
    ) -> None:
        violations.append(
            CompletionViolation(
                code=code,
                message=message,
                related_ids=list(related_ids),
            )
        )

    def _validate_session(
        self,
        run: ResearchRun,
        result: ScientificCompletedResult,
        violations: list[CompletionViolation],
    ) -> None:
        session = result.session
        if session.module != AgentOwner.SCIENTIFIC:
            self._add(
                violations,
                CompletionViolationCode.INVALID_SESSION,
                "completed result must come from a scientific session",
                session.id,
            )
        if session.status != SessionStatus.COMPLETED:
            self._add(
                violations,
                CompletionViolationCode.INVALID_SESSION,
                "completed result requires SessionStatus.completed",
                session.id,
            )
        if run.scientific_session is not None and run.scientific_session.id != session.id:
            self._add(
                violations,
                CompletionViolationCode.INVALID_SESSION,
                "completed result does not resume the run's scientific session",
                run.scientific_session.id,
                session.id,
            )

    def _validate_control_state(
        self,
        run: ResearchRun,
        violations: list[CompletionViolation],
    ) -> None:
        active = [
            item.id
            for item in run.work_requests
            if item.status
            in {
                WorkRequestStatus.REQUESTED,
                WorkRequestStatus.COMPILING,
                WorkRequestStatus.EXECUTING,
                WorkRequestStatus.STABLE,
            }
        ]
        if active:
            self._add(
                violations,
                CompletionViolationCode.ACTIVE_CONTROL_STATE,
                "active work requests prevent completion",
                *active,
            )
        if run.pending_question is not None:
            self._add(
                violations,
                CompletionViolationCode.ACTIVE_CONTROL_STATE,
                "a pending user question prevents completion",
                run.pending_question.id,
            )
        nonterminal = [] if run.workflow is None else [
            task.id
            for task in run.workflow.tasks
            if task.status
            in {
                TaskStatus.PENDING,
                TaskStatus.RUNNING,
                TaskStatus.NEEDS_USER_INPUT,
            }
        ]
        if nonterminal:
            self._add(
                violations,
                CompletionViolationCode.ACTIVE_CONTROL_STATE,
                "non-terminal tasks prevent completion",
                *nonterminal,
            )

    def _validate_opinion(
        self,
        opinion: ScientificOpinion,
        violations: list[CompletionViolation],
    ) -> None:
        # ScientificOpinion already enforces the verdict/evidence and
        # acknowledged-task/limitations combinations. Revalidate here so this
        # gate remains explicit even when fed a model created via model_construct.
        try:
            ScientificOpinion.model_validate(opinion.model_dump())
        except Exception as error:
            self._add(
                violations,
                CompletionViolationCode.INVALID_OPINION,
                f"invalid scientific opinion: {error}",
            )

    def _validate_evidence(
        self,
        run: ResearchRun,
        result: ScientificCompletedResult,
        violations: list[CompletionViolation],
    ) -> list[ArtifactRef]:
        evidence: list[ArtifactRef] = []
        turn_observed = set(result.observed_artifact_ids)
        run_observed = set(run.scientific_observed_artifact_ids)
        for artifact_id in result.opinion.evidence_artifact_ids:
            artifact = run.artifacts.get(artifact_id)
            if artifact is None or artifact.run_id != run.run_id:
                self._add(
                    violations,
                    CompletionViolationCode.UNKNOWN_EVIDENCE,
                    "opinion evidence is not a registered artifact of this run",
                    artifact_id,
                )
                continue
            evidence.append(artifact)
            if artifact_id not in turn_observed or artifact_id not in run_observed:
                self._add(
                    violations,
                    CompletionViolationCode.UNOBSERVED_EVIDENCE,
                    "opinion evidence must be present in both observed traces",
                    artifact_id,
                )
        return evidence

    def _validate_unresolved_tasks(
        self,
        run: ResearchRun,
        opinion: ScientificOpinion,
        violations: list[CompletionViolation],
    ) -> list[WorkTaskOutcome]:
        tasks = [] if run.workflow is None else run.workflow.tasks
        unresolved = [
            task for task in tasks if task.status in {TaskStatus.FAILED, TaskStatus.BLOCKED}
        ]
        acknowledged = set(opinion.acknowledged_task_ids)
        missing = [task.id for task in unresolved if task.id not in acknowledged]
        if missing:
            self._add(
                violations,
                CompletionViolationCode.UNACKNOWLEDGED_TASK,
                "failed or blocked tasks must be acknowledged",
                *missing,
            )
        if unresolved and not opinion.limitations:
            self._add(
                violations,
                CompletionViolationCode.MISSING_LIMITATIONS,
                "failed or blocked tasks require explicit limitations",
                *(task.id for task in unresolved),
            )

        issues: list[WorkTaskOutcome] = []
        for task in unresolved:
            if task.id not in acknowledged:
                continue
            attempt = task.attempts[-1] if task.attempts else None
            if attempt is None or attempt.error is None:
                self._add(
                    violations,
                    CompletionViolationCode.INCONSISTENT_TASK_RESULT,
                    "failed or blocked task lacks a terminal error",
                    task.id,
                )
                continue
            issues.append(
                WorkTaskOutcome(
                    task_id=task.id,
                    status=task.status.value,
                    summary=task.goal,
                    error=attempt.error,
                    warnings=list(task.warnings),
                )
            )
        return issues

    def _validate_completed_tasks(
        self,
        run: ResearchRun,
        violations: list[CompletionViolation],
    ) -> None:
        if run.workflow is None:
            return
        for task in run.workflow.tasks:
            if task.status != TaskStatus.COMPLETED:
                continue
            attempt = task.attempts[-1] if task.attempts else None
            if (
                attempt is None
                or attempt.status
                not in {AttemptStatus.COMPLETED, AttemptStatus.COMPLETED_WITH_WARNINGS}
                or attempt.error is not None
                or attempt.finished_at is None
            ):
                self._add(
                    violations,
                    CompletionViolationCode.INCONSISTENT_TASK_RESULT,
                    "completed task lacks a valid terminal attempt",
                    task.id,
                )
                continue

            owner = self._owners.get(task.capability)
            if owner is None:
                self._add(
                    violations,
                    CompletionViolationCode.INCONSISTENT_TASK_RESULT,
                    "completed task capability has no registered owner",
                    task.id,
                    task.capability.value,
                )
                continue
            for artifact_id in attempt.artifact_ids:
                artifact = run.artifacts.get(artifact_id)
                if (
                    artifact is None
                    or artifact.run_id != run.run_id
                    or artifact.task_id != task.id
                    or artifact.attempt_number != attempt.number
                    or artifact.producer != owner
                ):
                    self._add(
                        violations,
                        CompletionViolationCode.INCONSISTENT_TASK_RESULT,
                        "completed task artifact has invalid provenance or owner",
                        task.id,
                        artifact_id,
                    )

    def _validate_ids(
        self,
        run: ResearchRun,
        result: ScientificCompletedResult,
        violations: list[CompletionViolation],
    ) -> None:
        # Duplicate detection only applies to opinion fields the LLM authors
        # directly: evidence_artifact_ids and acknowledged_task_ids. The two
        # observed traces are deduplicated upstream (set semantics), so they
        # cannot contain duplicates here.
        groups = {
            "opinion evidence": result.opinion.evidence_artifact_ids,
            "acknowledged tasks": result.opinion.acknowledged_task_ids,
        }
        for label, values in groups.items():
            duplicates = sorted({value for value in values if values.count(value) > 1})
            if duplicates:
                self._add(
                    violations,
                    CompletionViolationCode.INVALID_OPINION,
                    f"{label} contains duplicate ids",
                    *duplicates,
                )

        tasks = {} if run.workflow is None else {task.id: task for task in run.workflow.tasks}
        invalid_acknowledged = [
            task_id
            for task_id in result.opinion.acknowledged_task_ids
            if task_id not in tasks
            or tasks[task_id].status not in {TaskStatus.FAILED, TaskStatus.BLOCKED}
        ]
        if invalid_acknowledged:
            self._add(
                violations,
                CompletionViolationCode.UNACKNOWLEDGED_TASK,
                "acknowledged ids must name failed or blocked tasks",
                *invalid_acknowledged,
            )

        for label, values in (
            ("turn observed trace", result.observed_artifact_ids),
            ("run observed trace", run.scientific_observed_artifact_ids),
        ):
            invalid = [
                artifact_id
                for artifact_id in values
                if artifact_id not in run.artifacts
                or run.artifacts[artifact_id].run_id != run.run_id
            ]
            if invalid:
                self._add(
                    violations,
                    CompletionViolationCode.UNKNOWN_EVIDENCE,
                    f"{label} contains unknown or cross-run ids",
                    *invalid,
                )


class FinalReportRenderer:
    """Render typed completion facts without another LLM call."""

    def render(self, data: FinalReportData) -> RenderedFinalReport:
        opinion = data.opinion
        lines = [
            "# Research Run Final Report",
            "",
            f"- Run: `{data.run_id}`",
            f"- Verdict: `{opinion.verdict.value}`",
            "",
            "## Goal",
            "",
            data.goal,
            "",
            "## Scientific opinion",
            "",
            opinion.statement,
            "",
            "## Evidence",
            "",
        ]
        if data.evidence:
            for artifact in data.evidence:
                lines.extend(
                    [
                        f"- `{artifact.id}` — {artifact.summary}",
                        f"  - kind: `{artifact.kind}`",
                        f"  - producer: `{artifact.producer.value}`",
                        f"  - sha256: `{artifact.sha256}`",
                        f"  - uri: `{artifact.uri}`",
                    ]
                )
        else:
            lines.append("- No evidence artifacts cited.")

        self._section(lines, "Limitations", opinion.limitations)
        self._section(lines, "Unresolved questions", opinion.unresolved_questions)
        self._section(lines, "Recommended next steps", opinion.recommended_next_steps)

        lines.extend(["", "## Execution issues", ""])
        if data.execution_issues:
            for issue in data.execution_issues:
                lines.append(f"- `{issue.task_id}` ({issue.status}): {issue.summary}")
        else:
            lines.append("- None.")

        content = "\n".join(lines).rstrip() + "\n"
        return RenderedFinalReport(
            candidate=ArtifactCandidate(
                kind="final_report",
                path="final_report.md",
                media_type="text/markdown",
                summary=f"Deterministic final report for {data.run_id}",
                metadata={"source_type": "final_report"},
            ),
            content=content,
        )

    @staticmethod
    def _section(lines: list[str], title: str, values: list[str]) -> None:
        lines.extend(["", f"## {title}", ""])
        lines.extend(f"- {value}" for value in values)
        if not values:
            lines.append("- None.")
