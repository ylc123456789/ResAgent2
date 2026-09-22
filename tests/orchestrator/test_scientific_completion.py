"""Final completion consumes registered opinion, observation and requirement snapshots."""

from resagent2_contracts import RunPermissions, ExecutionLimits
import json
from datetime import UTC, datetime

import pytest

from resagent2_contracts import (
    AgentOwner,
    AgentResult,
    ArtifactCandidate,
    Attempt,
    ConclusionRequirements,
    ModuleError,
    SYSTEM_ARTIFACT_PROVENANCE,
    ScientificOpinion,
    SessionRef,
    WorkOutcome,
    WorkRequest,
    WorkRequestDraft,
    WorkTaskOutcome,
    Workflow,
    WorkflowAgentDefinition,
    WorkflowAgentRegistry,
    WorkflowTask,
)
from resagent2_orchestrator import (
    ArtifactRegistry, ArtifactRegistrationError, CompletionViolationCode, FinalReportRenderer,
    ResearchRun, ScientificCompletionValidator,
)
from resagent2_contracts import (
    ResearchRequest,
    RunBudget,
)
from resagent2_orchestrator.handoffs import system_artifact


def candidate(kind, data):
    return ArtifactCandidate(kind=kind, path=kind+".json", media_type="application/json", summary=kind,
        content=data.model_dump_json() if hasattr(data, "model_dump_json") else json.dumps(data))


@pytest.fixture
def prepared(tmp_path):
    now = datetime.now(UTC)
    registry = ArtifactRegistry(tmp_path / "artifacts")
    session = SessionRef(id="session_scientific", module="scientific", state_uri="memory://scientific",
        status="completed", created_at=now, updated_at=now)
    run = ResearchRun(run_id='run_gate', request=ResearchRequest(goal='Evaluate the method', budget=RunBudget(max_llm_calls=20, timeout_seconds=60), permissions=RunPermissions(execute_commands=True, prepare_environment=True), execution_limits=ExecutionLimits(max_tasks=5, max_attempts_per_task=2)), status='running', scientific_session=session, created_at=now, updated_at=now)
    run.conclusion_requirements_ref = system_artifact(registry, run, "conclusion_requirements", ConclusionRequirements())
    gate = ScientificCompletionValidator(WorkflowAgentRegistry(definitions=[WorkflowAgentDefinition(workflow_agent_kind="experiment")]))
    return registry, run, gate


def registered_result(prepared, *, verdict="inconclusive", cited=(), observed=(), limitations=()):
    registry, run, gate = prepared
    opinion = ScientificOpinion(verdict=verdict, statement="The evidence supports this judgment",
        evidence_artifact_ids=list(cited), limitations=list(limitations))
    refs = [registry.register_scientific(item, run_id=run.run_id, session_id=run.scientific_session.id)
        for item in [candidate("scientific_opinion", opinion), candidate("observation_trace", {"observed_artifact_ids": list(observed)})]]
    run.artifacts.update({ref.id: ref for ref in refs})
    return AgentResult(status="completed", report="Conclusion", artifacts=refs, session=run.scientific_session), refs


def evidence(prepared, *, run_id=None):
    registry, run, _ = prepared
    ref = registry.register_scientific(candidate("literature_search", {"papers": ["paper"]}),
        run_id=run_id or run.run_id, session_id=run.scientific_session.id)
    run.artifacts[ref.id] = ref
    return ref


@pytest.mark.parametrize("verdict", ["supports", "refutes", "inconclusive", "not_applicable"])
def test_all_verdicts_pass_with_valid_registered_evidence(prepared, verdict):
    _, run, gate = prepared
    ref = evidence(prepared)
    run.scientific_observed_artifact_ids = [ref.id]
    result, refs = registered_result(prepared, verdict=verdict, cited=[ref.id], observed=[ref.id])
    accepted = gate.validate(run, result, refs)
    assert accepted.ok
    assert accepted.report.evidence == [ref]


@pytest.mark.parametrize("run_observed,trace_observed,cited", [(False, True, True), (True, False, True), (True, True, False)])
def test_required_evidence_must_be_registered_observed_and_cited(prepared, run_observed, trace_observed, cited):
    registry, run, gate = prepared
    ref = evidence(prepared)
    run.conclusion_requirements_ref = system_artifact(registry, run, "conclusion_requirements",
        ConclusionRequirements(required_evidence_kinds=["literature_search"]))
    run.scientific_observed_artifact_ids = [ref.id] if run_observed else []
    result, refs = registered_result(prepared, cited=[ref.id] if cited else [], observed=[ref.id] if trace_observed else [])
    actual = gate.validate(run, result, refs)
    assert not actual.ok
    assert CompletionViolationCode.MISSING_EVIDENCE_KIND in {v.code for v in actual.violations}


def test_unknown_observation_is_rejected(prepared):
    _, run, gate = prepared
    result, refs = registered_result(prepared, observed=["artifact_fake"])
    assert not gate.validate(run, result, refs).ok


def test_cross_run_evidence_is_rejected(prepared):
    _, run, gate = prepared
    ref = evidence(prepared, run_id="run_other")
    run.scientific_observed_artifact_ids = [ref.id]
    result, refs = registered_result(prepared, verdict="supports", cited=[ref.id], observed=[ref.id])
    assert not gate.validate(run, result, refs).ok


@pytest.mark.parametrize("status", ["pending", "running", "needs_user_input"])
def test_nonterminal_task_prevents_completion(prepared, status):
    _, run, gate = prepared
    run.workflow = Workflow(run_id=run.run_id, revision=1, created_from="work_test", tasks=[WorkflowTask(
        id="task_test", work_request_id="work_test", workflow_agent_kind="experiment", instruction="Measure", status=status)])
    result, refs = registered_result(prepared)
    actual = gate.validate(run, result, refs)
    assert CompletionViolationCode.ACTIVE_CONTROL_STATE in {v.code for v in actual.violations}


def test_active_work_request_prevents_completion(prepared):
    _, run, gate = prepared
    run.work_requests = [WorkRequest(id="work_test", run_id=run.run_id, scientific_session_id=run.scientific_session.id,
        request=WorkRequestDraft(objective="Measure", expected_evidence=["metric"]), created_at=run.created_at, updated_at=run.updated_at)]
    result, refs = registered_result(prepared)
    assert not gate.validate(run, result, refs).ok


def test_completed_task_requires_terminal_attempt_history(prepared):
    _, run, gate = prepared
    task = WorkflowTask(id="task_test", work_request_id="work_test", workflow_agent_kind="experiment", instruction="Measure", status="completed")
    run.workflow = Workflow(run_id=run.run_id, revision=1, created_from="work_test", tasks=[task])
    result, refs = registered_result(prepared)
    assert not gate.validate(run, result, refs).ok
    run.workflow.tasks[0].attempts = [Attempt(number=1, status="completed", started_at=run.created_at, finished_at=run.updated_at, report="Executed")]
    assert gate.validate(run, result, refs).ok


def test_failed_task_requires_limitations_and_is_reported(prepared):
    _, run, gate = prepared
    issue = WorkTaskOutcome(task_id="task_failed", status="failed", summary="Command failed",
        error=ModuleError(code="tool_failed", message="exit 1", retryable=False))
    run.work_requests = [WorkRequest(id="work_test", run_id=run.run_id, scientific_session_id=run.scientific_session.id,
        request=WorkRequestDraft(objective="Measure", expected_evidence=["metric"]), status="consumed", workflow_revision=1,
        outcome=WorkOutcome(work_request_id="work_test", workflow_revision=1, summary="Failed", tasks=[issue]),
        created_at=run.created_at, updated_at=run.updated_at)]
    result, refs = registered_result(prepared)
    assert not gate.validate(run, result, refs).ok
    result, refs = registered_result(prepared, limitations=["Execution failed"])
    actual = gate.validate(run, result, refs)
    assert actual.ok and actual.report.execution_issues == [issue]


@pytest.mark.parametrize("fault", ["missing_requirement", "missing_opinion", "foreign_session", "corrupt_opinion"])
def test_completion_rejects_missing_or_corrupt_authority(prepared, fault):
    from pathlib import Path
    _, run, gate = prepared
    result, refs = registered_result(prepared)
    if fault == "missing_requirement": run.conclusion_requirements_ref = None
    elif fault == "missing_opinion": refs = refs[1:]
    elif fault == "foreign_session": result.session = result.session.model_copy(update={"id": "session_foreign"})
    else: Path(refs[0].uri.removeprefix("file://")).write_text("{}")
    assert not gate.validate(run, result, refs).ok


def test_renderer_and_registration_are_deterministic(prepared):
    registry, run, gate = prepared
    result, refs = registered_result(prepared)
    accepted = gate.validate(run, result, refs)
    first = FinalReportRenderer().render(accepted.report)
    assert first == FinalReportRenderer().render(accepted.report)
    one = registry.register_final_report(first.candidate, first.content, run_id=run.run_id)
    two = registry.register_final_report(first.candidate, first.content, run_id=run.run_id)
    assert one == two and len(one.sha256) == 64


def test_scientific_registration_is_idempotent_and_rejects_system_kind(prepared):
    registry, run, _ = prepared
    first = evidence(prepared)
    assert first == evidence(prepared)
    assert first.task_id is None and first.producer == AgentOwner.SCIENTIFIC
    with pytest.raises(ArtifactRegistrationError):
        registry.register_scientific(candidate("answer", {}), run_id=run.run_id, session_id=run.scientific_session.id)


@pytest.mark.parametrize("kind", sorted(SYSTEM_ARTIFACT_PROVENANCE))
def test_each_system_kind_supports_its_defined_scope_and_replay(prepared, kind):
    registry, run, _ = prepared
    source, scopes = SYSTEM_ARTIFACT_PROVENANCE[kind]
    for scope in scopes:
        fields = {"run": {}, "task": {"task_id": "task_test"}, "attempt": {"task_id": "task_test", "attempt_number": 1}, "session": {"session_id": run.scientific_session.id}}[scope]
        first = registry.register_system_artifact(candidate(kind, {}), run_id=run.run_id, source_type=source, **fields)
        assert first == registry.register_system_artifact(candidate(kind, {}), run_id=run.run_id, source_type=source, **fields)
        with pytest.raises(ValueError):
            registry.register_system_artifact(candidate(kind, {}), run_id=run.run_id, source_type="forged", **fields)
