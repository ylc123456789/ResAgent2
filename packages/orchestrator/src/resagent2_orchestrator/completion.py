"""Check final artifact evidence and render the accepted research report."""

from dataclasses import dataclass
from pydantic import BaseModel, ConfigDict, Field
from resagent2_contracts import (
    AgentOwner, ArtifactCandidate, ArtifactRef, AttemptStatus, ConclusionRequirements,
    ModuleStatus, ObservationTrace, ScientificOpinion, SessionStatus, TaskStatus,
    WorkRequestStatus, WorkTaskOutcome, missing_required_evidence_kinds,
)
from .handoffs import read_json, check_acceptance
from .models import CompletionViolation, CompletionViolationCode


class FinalReportData(BaseModel):
    """Validated inputs to deterministic final-report rendering."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    goal: str
    opinion: ScientificOpinion
    evidence: list[ArtifactRef]
    execution_issues: list[WorkTaskOutcome] = Field(default_factory=list)


@dataclass(frozen=True)
class CompletionValidation:
    """Completion violations or the accepted report data."""
    violations: tuple[CompletionViolation, ...] = ()
    report: FinalReportData | None = None

    @property
    def ok(self):
        return not self.violations and self.report is not None


@dataclass(frozen=True)
class RenderedFinalReport:
    """Rendered report bytes and registration metadata."""
    candidate: ArtifactCandidate
    content: str


class ScientificCompletionValidator:
    """Check evidence provenance and terminal execution state."""
    def __init__(self, registry, artifact_registry):
        self._artifact_registry = artifact_registry
        self._kinds = {item.workflow_agent_kind for item in registry.definitions}

    def validate(self, run, result, refs):
        violations = []

        def reject(code, message, ids=(), *, subject=None):
            violations.append(CompletionViolation(
                code=code, message=message, subject=subject, related_ids=list(ids),
            ))

        if result.status not in {ModuleStatus.COMPLETED, ModuleStatus.COMPLETED_WITH_WARNINGS}:
            reject(CompletionViolationCode.INVALID_OPINION, "Scientific has not completed")
        session = result.session
        if (session is None or session.module != AgentOwner.SCIENTIFIC or session.status != SessionStatus.COMPLETED
                or run.scientific_session is None or session.id != run.scientific_session.id):
            reject(CompletionViolationCode.INVALID_SESSION, "completed result requires its bound Scientific session")
        if run.pending_question or any(work.status not in {WorkRequestStatus.CONSUMED, WorkRequestStatus.FAILED} for work in run.work_requests):
            reject(CompletionViolationCode.ACTIVE_CONTROL_STATE, "active control state prevents completion")
        if any(task.status in {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.NEEDS_USER_INPUT} for task in (run.workflow.tasks if run.workflow else [])):
            reject(CompletionViolationCode.ACTIVE_CONTROL_STATE, "non-terminal tasks prevent completion")
        try:
            opinions = [ref for ref in refs if ref.kind == "scientific_opinion"]
            traces = [ref for ref in refs if ref.kind == "observation_trace"]
            if len(opinions) != 1 or len(traces) != 1:
                raise ValueError("completion requires one opinion and one execution observation trace")
            for ref in [*opinions, *traces]:
                if run.artifacts.get(ref.id) != ref or ref.producer != AgentOwner.SCIENTIFIC or not session or ref.session_id != session.id:
                    raise ValueError("Scientific completion artifact has invalid ownership")
            opinion = read_json(opinions[0], ScientificOpinion)
            observed = set(read_json(traces[0], ObservationTrace).observed_artifact_ids)
            cited = set(opinion.evidence_artifact_ids)
            if len(cited) != len(opinion.evidence_artifact_ids):
                raise ValueError("duplicate opinion citations")
            if not observed <= set(run.artifacts):
                raise ValueError("observation trace contains unknown evidence")
            if any(run.artifacts[key].run_id != run.run_id for key in observed | cited):
                raise ValueError("evidence belongs to another Run")
            valid = observed & set(run.scientific_observed_artifact_ids)
            if not cited <= valid:
                reject(CompletionViolationCode.UNOBSERVED_EVIDENCE, "opinion cites unobserved evidence", cited-valid)
            requirement = run.conclusion_requirements_ref
            if requirement is None or run.artifacts.get(requirement.id) != requirement or requirement.kind != "conclusion_requirements" or requirement.run_id != run.run_id:
                raise ValueError("Run conclusion requirement binding missing or invalid")
            requirements = read_json(requirement, ConclusionRequirements)
            for name in self._artifact_registry.missing_required_artifacts(
                requirements.required_artifacts, run_id=run.run_id, artifacts=run.artifacts,
            ):
                reject(CompletionViolationCode.REQUIRED_ARTIFACT_MISSING,
                       "required artifact was not produced", subject=name)
            missing = missing_required_evidence_kinds(requirements.required_evidence_kinds, run_id=run.run_id, artifacts=run.artifacts.values(),
                                                      observed_artifact_ids=valid, cited_artifact_ids=cited)
            if missing:
                reject(CompletionViolationCode.MISSING_EVIDENCE_KIND, "missing observed and cited evidence kinds", missing)
        except (ValueError, OSError, KeyError) as error:
            reject(CompletionViolationCode.INVALID_OPINION, str(error))
            return CompletionValidation(tuple(violations))
        for task in run.workflow.tasks if run.workflow else []:
            if task.status != TaskStatus.COMPLETED:
                continue
            last = task.attempts[-1] if task.attempts else None
            if task.workflow_agent_kind not in self._kinds or last is None or last.status not in {AttemptStatus.COMPLETED, AttemptStatus.COMPLETED_WITH_WARNINGS} or last.finished_at is None or last.error:
                reject(CompletionViolationCode.INCONSISTENT_TASK_RESULT, "completed task lacks valid execution history", [task.id])
                continue
            try:
                outputs = [run.artifacts[key] for key in last.artifact_ids]
                if any(ref.run_id != run.run_id or ref.task_id != task.id or ref.attempt_number != last.number
                       or ref.producer.value != task.workflow_agent_kind.value for ref in outputs
                       if ref.kind != "question"):
                    raise ValueError("completed task has foreign output artifacts")
                check_acceptance(run, task, last, outputs)
            except (ValueError, OSError, KeyError) as error:
                reject(CompletionViolationCode.INCONSISTENT_TASK_RESULT, str(error), [task.id])
        issues = [item for work in run.work_requests if work.outcome for item in work.outcome.tasks if item.status != "completed"]
        if issues and not opinion.limitations:
            reject(CompletionViolationCode.MISSING_LIMITATIONS, "failed execution work requires stated limitations")
        if violations:
            return CompletionValidation(tuple(violations))
        return CompletionValidation(report=FinalReportData(run_id=run.run_id, goal=run.request.goal, opinion=opinion,
                                                          evidence=[run.artifacts[key] for key in opinion.evidence_artifact_ids], execution_issues=issues))


class FinalReportRenderer:
    """Render an accepted opinion and its provenance as Markdown."""
    def render(self, data):
        opinion = data.opinion
        lines = ["# Research Run Final Report", "", f"- Run: `{data.run_id}`", f"- Verdict: `{opinion.verdict.value}`",
                 "", "## Goal", "", data.goal, "", "## Scientific opinion", "", opinion.statement, "", "## Evidence", ""]
        for ref in data.evidence:
            lines.extend([f"- `{ref.id}`: {ref.summary}", f"  kind: `{ref.kind}`; producer: `{ref.producer.value}`",
                          f"  sha256: `{ref.sha256}`", f"  uri: `{ref.uri}`"])
        if not data.evidence:
            lines.append("- No evidence artifacts cited.")
        for title, values in [("Limitations", opinion.limitations), ("Unresolved questions", opinion.unresolved_questions),
                              ("Recommended next steps", opinion.recommended_next_steps)]:
            lines.extend(["", "## " + title, "", *["- " + item for item in values or ["None."]]])
        lines.extend(["", "## Execution issues", ""])
        lines.extend(f"- `{issue.task_id}` ({issue.status}): {issue.summary}" for issue in data.execution_issues)
        if not data.execution_issues:
            lines.append("- None.")
        content = "\n".join(lines) + "\n"
        return RenderedFinalReport(ArtifactCandidate(kind="final_report", path="final_report.md", media_type="text/markdown",
                                                     summary=f"Final report for {data.run_id}", metadata={"source_type": "final_report"}), content)
