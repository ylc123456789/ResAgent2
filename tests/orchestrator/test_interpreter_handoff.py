"""Terminal work handoffs preserve original Task reports without aggregate artifacts."""
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname
import pytest
from resagent2_contracts import (
    AgentOwner, AgentResult, ArtifactCandidate, Attempt, WorkRequest, WorkRequestDraft,
    WorkRequestStatus, Workflow, WorkflowTask, WorkflowAgentRegistry, WorkflowAgentDefinition,
    ResearchRequest, RunBudget, RunPermissions, PendingQuestion, ControlSignal, SessionRef,
)
from resagent2_orchestrator import DeterministicWorkInterpreter, JsonRunStore, ResearchController, ResearchRun, WorkflowScheduler
from resagent2_orchestrator.handoffs import system_artifact


class ProcessCrash(BaseException):
    pass


class AskingScientific:
    def __init__(self, crash=False):
        self.requests = []
        self.crash = crash

    def invoke(self, request):
        self.requests.append(request)
        if self.crash:
            raise ProcessCrash()
        now = datetime.now(UTC)
        sid = request.parent_session_id or "session_handoff"
        return AgentResult(status="needs_user_input", report="Need a decision",
                           session=SessionRef(id=sid, module="scientific", state_uri=f"session://{sid}",
                                              status="paused", created_at=now, updated_at=now),
                           control=ControlSignal(action="ask_user", source_session_id=sid, source_turn=1,
                                                 source_call_id="ask_1", question=PendingQuestion(
                                                     id="question_handoff", run_id=request.run_id, session_id=sid,
                                                     text="Which metric?", requested_fields=["metric"], created_at=now)))


def engine(tmp_path, scientific):
    return ResearchController(scientific_port=scientific, compiler=None,
        scheduler=WorkflowScheduler(bindings={}, store=JsonRunStore(tmp_path/"runs"), artifact_root=tmp_path/"artifacts"),
        registry=WorkflowAgentRegistry(definitions=[WorkflowAgentDefinition(workflow_agent_kind="experiment")]),
        interpreter=DeterministicWorkInterpreter())


def stable_run(controller):
    now = datetime.now(UTC)
    work = WorkRequest(id="work_handoff", run_id="run_handoff", scientific_session_id="session_handoff",
                       request=WorkRequestDraft(objective="Measure accuracy", expected_evidence=["metrics"]),
                       status="executing", workflow_revision=1, created_at=now, updated_at=now)
    run = ResearchRun(run_id="run_handoff", request=ResearchRequest(goal="Evaluate", permissions=RunPermissions(), budget=RunBudget(max_llm_calls=10, timeout_seconds=60)),
                      status="running", scientific_session=SessionRef(id="session_handoff", module="scientific", state_uri="session://session_handoff",
                                                                     status="paused", created_at=now, updated_at=now),
                      work_requests=[work], created_at=now, updated_at=now)
    run.initialization.complete = True
    controller.scheduler.store.save(run)
    run.conclusion_requirements_ref = system_artifact(controller.scheduler.artifact_registry, run,
        "conclusion_requirements", {"schema_version": "26.0", "required_artifacts": [], "required_evidence_kinds": []})
    output = controller.scheduler.artifact_registry.register(
        ArtifactCandidate(kind="metrics", path="metrics.json", media_type="application/json", summary="Accuracy measurement", content='{"accuracy":0.8}'),
        grant=None, producer=AgentOwner.EXPERIMENT, run_id=run.run_id, task_id="task_measure", attempt_number=1,
        index=1, run=run)
    task = WorkflowTask(id="task_measure", work_request_id=work.id, workflow_agent_kind="experiment", instruction="Measure accuracy",
                        status="completed", attempts=[Attempt(number=1, status="completed", started_at=now, finished_at=now,
                                                               report="Original report.  \nExact: 0.80000.\n", artifact_ids=[output.id], agent_started=True)])
    run.workflow = Workflow(run_id=run.run_id, revision=1, created_from=work.id, tasks=[task])
    task = run.workflow.tasks[0]
    work = run.work_requests[0]
    controller.scheduler._persist_attempt_report(run, task, task.attempts[0])
    work.outcome = controller.scheduler._build_work_outcome(run, work.id)
    work.status = WorkRequestStatus.STABLE
    controller.scheduler.store.save(run)
    return run


def test_restart_reuses_terminal_result_and_its_original_reports(tmp_path):
    first = engine(tmp_path, AskingScientific(crash=True))
    run = stable_run(first)
    with pytest.raises(ProcessCrash):
        first.run_until_stable(run.run_id)
    saved = first.scheduler.store.load(run.run_id)
    original = saved.work_requests[0].outcome
    scientific = AskingScientific()
    second = engine(tmp_path, scientific)
    paused = second.run_until_stable(run.run_id)
    assert paused.status == "paused"
    assert paused.work_requests[0].status == "consumed"
    assert scientific.requests[0].work_results == [original]
    assert {ref.id for ref in original.artifacts} <= {ref.id for ref in scientific.requests[0].input_artifacts}
    assert not any(ref.kind in {"work_record", "work_feedback", "research_index", "question"} for ref in paused.artifacts.values())
    assert paused.llm_calls_used == 0
    report_id = original.tasks[0].attempts[0].report_artifact_id
    report = paused.artifacts[report_id]
    assert Path(url2pathname(urlparse(report.uri).path)).read_text() == run.workflow.tasks[0].attempts[0].report


def test_terminal_report_registered_once_and_not_listed_as_its_own_output(tmp_path):
    controller = engine(tmp_path, AskingScientific())
    run = stable_run(controller)
    task = run.workflow.tasks[0]
    before = set(run.artifacts)
    first = controller.scheduler._persist_attempt_report(run, task, task.attempts[0])
    second = controller.scheduler._persist_attempt_report(run, task, task.attempts[0])
    assert first == second and set(run.artifacts) == before
    assert first.id not in first.metadata["output_artifact_ids"]
    assert first.metadata["status"] == "completed"


def test_zero_task_failure_returns_navigation_without_fake_report(tmp_path):
    controller = engine(tmp_path, AskingScientific())
    run = stable_run(controller)
    run.workflow = None
    work = run.work_requests[0]
    from resagent2_contracts import ModuleError
    work.status = WorkRequestStatus.FAILED
    work.outcome = None
    work.error = ModuleError(code="contract_error", message="Compiler rejected the request", retryable=False)
    work.workflow_revision = None
    controller.scheduler.store.save(run)
    controller._prepare_research_handoff(run)
    result = run.work_requests[0].outcome
    assert result.status == "failed" and result.tasks == [] and result.artifacts == []
    assert result.error == work.error
