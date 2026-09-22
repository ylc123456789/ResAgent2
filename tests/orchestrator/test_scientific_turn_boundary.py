"""Result acceptance precedes delivery acknowledgement and state transitions."""

from resagent2_contracts import RunPermissions, ExecutionLimits
from resagent2_orchestrator.models import RunUsage
import json
from datetime import UTC, datetime

import pytest

from resagent2_contracts import (
    AgentOwner,
    AgentResult,
    ArtifactCandidate,
    ConclusionRequirements,
    ControlSignal,
    ModuleError,
    QuestionDraft,
    ResearchRequest,
    RunBudget,
    SessionRef,
    SessionStatus,
    WorkOutcome,
    WorkRequest,
    WorkRequestDraft,
    WorkTaskOutcome,
    WorkflowAgentRegistry,
)
from resagent2_orchestrator import JsonRunStore, ResearchController, ResearchRun, WorkflowScheduler
from resagent2_orchestrator.handoffs import system_artifact


def candidate(kind, data):
    return ArtifactCandidate(kind=kind, path=kind+".json", media_type="application/json", summary=kind,
        content=data.model_dump_json() if hasattr(data,"model_dump_json") else json.dumps(data))


def prepared(tmp_path):
    now = datetime.now(UTC)
    controller = ResearchController(scientific_port=None, compiler=None,
        scheduler=WorkflowScheduler(bindings={}, store=JsonRunStore(tmp_path/"runs"), artifact_root=tmp_path/"artifacts"),
        registry=WorkflowAgentRegistry(definitions=[]))
    session = SessionRef(id="session_scientific_run_boundary", module="scientific", status="paused",
        state_uri="session://session_scientific_run_boundary", created_at=now, updated_at=now)
    run = ResearchRun(run_id='run_boundary', status='running', request=ResearchRequest(goal='Evaluate', budget=RunBudget(max_llm_calls=20, timeout_seconds=60), permissions=RunPermissions(execute_commands=True, prepare_environment=True), execution_limits=ExecutionLimits(max_tasks=4, max_attempts_per_task=2)), scientific_session=session, usage=RunUsage(requests={f'fixture_{i}:0': 'succeeded' for i in range(2)}), work_requests=[WorkRequest(id='work_1', run_id='run_boundary', scientific_session_id=session.id, request=WorkRequestDraft(objective='Run', expected_evidence=['result']), status='stable', workflow_revision=1, outcome=WorkOutcome(work_request_id='work_1', workflow_revision=1, summary='Executed', tasks=[WorkTaskOutcome(task_id='task_one', status='completed', summary='Done')]), created_at=now, updated_at=now)], created_at=now, updated_at=now)
    run.conclusion_requirements_ref = system_artifact(controller.scheduler.artifact_registry, run,
        "conclusion_requirements", ConclusionRequirements())
    request = controller._scientific_request(run)
    controller.scheduler.store.save(run)
    return controller, run, request


def reply(run, status):
    session_status = {"request_work":"paused", "needs_user_input":"paused", "completed":"completed", "failed":"failed"}[status]
    fields = dict(status=status, report="Scientific turn report", llm_calls=3,
        session=run.scientific_session.model_copy(update={"status":SessionStatus(session_status)}))
    artifacts = [candidate("observation_trace", {"observed_artifact_ids":[]})]
    if status == "completed": artifacts.append(candidate("scientific_opinion", {"verdict":"inconclusive","statement":"Not enough evidence"}))
    elif status == "failed": fields["error"] = ModuleError(code="tool_failed",message="DISTINCT_ROOT_CAUSE",retryable=False,details={"stderr_tail":"Original diagnostic"})
    elif status == "needs_user_input":
        artifacts.append(candidate("question", QuestionDraft(text="Which metric?",requested_fields=["metric"])))
        fields["control"] = ControlSignal(action="ask_user",candidate_index=1)
    else:
        artifacts.append(candidate("work_request", {"assessment":{"statement":"Need more evidence"},"work_request":{"objective":"Repeat","expected_evidence":["result"]}}))
        fields["control"] = ControlSignal(action="request_work",candidate_index=1)
    return AgentResult(artifacts=artifacts, **fields)


def assert_unconsumed(run):
    assert run.status == "failed"
    assert run.llm_calls_used == 2
    assert run.work_requests[0].status == "stable"
    assert run.scientific_session.status == "paused"
    assert run.pending_question is None
    assert run.scientific_observed_artifact_ids == []
    assert run.delivered_answer_ids == []


@pytest.mark.parametrize("status", ["request_work","needs_user_input","completed","failed"])
@pytest.mark.parametrize("field,value", [("id","session_other"),("module",AgentOwner.CODING),("status",SessionStatus.ACTIVE)])
def test_all_statuses_reject_wrong_session_before_acknowledgement(tmp_path,status,field,value):
    controller,run,request = prepared(tmp_path)
    result = reply(run,status)
    result.session = result.session.model_copy(update={field:value})
    actual = controller._apply_turn(run.run_id,request,result)
    assert_unconsumed(actual)
    assert actual.terminal_error.code == "contract_error"


def test_invalid_envelope_is_revalidated_and_not_consumed(tmp_path):
    controller,run,request = prepared(tmp_path)
    result = reply(run,"completed").model_copy(update={"report":""})
    actual = controller._apply_turn(run.run_id,request,result)
    assert_unconsumed(actual)


def test_valid_question_acknowledges_work_feedback_once(tmp_path):
    controller,run,request = prepared(tmp_path)
    actual = controller._apply_turn(run.run_id,request,reply(run,"needs_user_input"))
    assert actual.status == "paused"
    assert actual.work_requests[0].status == "consumed"
    assert actual.llm_calls_used == 2
    assert actual.pending_question.requested_fields == ["metric"]
    assert actual.pending_question_ref.kind == "question"
    assert actual.terminal_error is None


@pytest.mark.parametrize("with_session", [False,True])
def test_failed_turn_keeps_root_cause_on_disk(tmp_path,with_session):
    controller,run,request = prepared(tmp_path)
    result = reply(run,"failed")
    if not with_session:
        result.session=None
        result.artifacts=[]
    controller._apply_turn(run.run_id,request,result)
    actual = controller.scheduler.store.load(run.run_id)
    assert actual.status == "failed" and actual.llm_calls_used == 2
    assert actual.terminal_error == result.error


def test_final_report_storage_failure_has_durable_reason(tmp_path,monkeypatch):
    controller,run,request = prepared(tmp_path)
    def fail(*args,**kwargs): raise OSError("REPORT_STORAGE_UNAVAILABLE")
    monkeypatch.setattr(controller.scheduler.artifact_registry,"register_final_report",fail)
    controller._apply_turn(run.run_id,request,reply(run,"completed"))
    actual = controller.scheduler.store.load(run.run_id)
    assert actual.status == "failed" and actual.llm_calls_used == 2
    assert "REPORT_STORAGE_UNAVAILABLE" in actual.terminal_error.message
    assert actual.final_opinion is None and actual.final_report_artifact_id is None


def test_unknown_observation_does_not_consume_feedback(tmp_path):
    controller,run,request = prepared(tmp_path)
    result = reply(run,"completed")
    result.artifacts[0].content=json.dumps({"observed_artifact_ids":["artifact_forged"]})
    actual = controller._apply_turn(run.run_id,request,result)
    assert_unconsumed(actual)


@pytest.mark.parametrize("calls", [-1,True,"3"])
def test_invalid_usage_does_not_charge_fabricated_calls(tmp_path,calls):
    controller,run,request = prepared(tmp_path)
    raw=reply(run,"completed").model_dump()
    raw["llm_calls"]=calls
    actual=controller._apply_turn(run.run_id,request,raw)
    assert actual.status == "failed"
    assert actual.llm_calls_used == 2
