"""The Controller commits reverse handoffs once and preserves their Run budget."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from resagent2_contracts import (
    AgentOwner, AgentResult, ArtifactCandidate, Attempt, CitedStatement,
    ConclusionRequirements, ControlSignal, QuestionDraft, ResearchRequest,
    RunBudget, RunPermissions, SessionRef, UserAnswer, WorkBrief, WorkFeedback,
    WorkRecord, WorkRequest, WorkRequestDraft, Workflow, WorkflowAgentDefinition,
    WorkflowAgentRegistry, WorkflowTask,
)
from resagent2_orchestrator import (
    JsonRunStore, LLMWorkInterpreter, ResearchController, ResearchRun,
    WorkflowScheduler,
)
from resagent2_orchestrator.handoffs import read_json, system_artifact

RUN_ID = "run_handoff"
WORK_ID = "work_measure"
SESSION_ID = "session_handoff"


class ProcessCrash(BaseException):
    """Fault injection that bypasses normal product error handling."""


class BriefClient:
    def __init__(self, *, invalid=False, after_response=None):
        self.prompts = []
        self.invalid = invalid
        self.after_response = after_response

    def next_action(self, prompt, action_type):
        self.prompts.append(prompt)
        payload = json.loads(prompt.split("\n", 1)[1].split("\nPrevious brief rejected", 1)[0])
        if self.after_response:
            self.after_response()
        return WorkBrief(statements=[CitedStatement(
            text="The completed work has an execution record.",
            artifact_ids=["artifact_invented" if self.invalid else payload["work_record_artifact_id"]],
        )])


class AskingScientific:
    def __init__(self, *, crash=False):
        self.requests = []
        self.crash = crash

    def invoke(self, request):
        self.requests.append(request)
        if self.crash:
            raise ProcessCrash("Scientific interrupted after durable handoff")
        now = datetime.now(UTC)
        return AgentResult(
            status="needs_user_input", report="Need a research decision",
            session=SessionRef(id=SESSION_ID, module="scientific", state_uri="session://handoff",
                               status="paused", created_at=now, updated_at=now),
            artifacts=[ArtifactCandidate(
                kind="question", path="question.json", media_type="application/json",
                summary="Research decision", content=QuestionDraft(
                    text="Should the interpretation use accuracy?", requested_fields=["metric"],
                ).model_dump_json(),
            )],
            control=ControlSignal(action="ask_user", candidate_index=0),
        )


class NoCompiler:
    def compile(self, *args, **kwargs):
        raise AssertionError("Stable work must not be compiled again")


def controller(tmp_path, client, scientific):
    scheduler = WorkflowScheduler(
        bindings={}, store=JsonRunStore(tmp_path / "state"),
        artifact_root=tmp_path / "artifacts", data_root=tmp_path / "data",
    )
    registry = WorkflowAgentRegistry(definitions=[WorkflowAgentDefinition(
        workflow_agent_kind="experiment", description="Analyze results",
    )])
    return ResearchController(
        scientific_port=scientific, compiler=NoCompiler(), scheduler=scheduler,
        registry=registry, interpreter=LLMWorkInterpreter(client),
    )


def stable_run(engine, *, max_calls=10, timeout=60):
    now = datetime.now(UTC)
    ref = engine.scheduler.artifact_registry.register(
        ArtifactCandidate(kind="data", path="metrics.json", media_type="application/json",
                          summary="Measured accuracy", content='{"accuracy":0.8}'),
        grant=None, producer=AgentOwner.EXPERIMENT, run_id=RUN_ID,
        task_id="task_measure", attempt_number=1, index=1, existing_ids=set(),
    )
    task = WorkflowTask(
        id="task_measure", work_request_id=WORK_ID, workflow_agent_kind="experiment",
        instruction="Measure accuracy", status="completed", attempts=[Attempt(
            number=1, status="completed", started_at=now, finished_at=now,
            report="Measured accuracy", artifact_ids=[ref.id],
        )],
    )
    run = ResearchRun(
        run_id=RUN_ID, status="running", created_at=now, updated_at=now,
        request=ResearchRequest(goal="Evaluate results", permissions=RunPermissions(),
                                budget=RunBudget(max_llm_calls=max_calls, timeout_seconds=timeout)),
        scientific_session=SessionRef(
            id=SESSION_ID, module="scientific", state_uri="session://handoff", status="paused",
            created_at=now, updated_at=now,
        ),
        artifacts={ref.id: ref},
        workflow=Workflow(run_id=RUN_ID, revision=1, created_from=WORK_ID, tasks=[task]),
    )
    run.work_requests.append(WorkRequest(
        id=WORK_ID, run_id=RUN_ID, scientific_session_id=SESSION_ID,
        request=WorkRequestDraft(objective="Measure accuracy", expected_evidence=["metrics"]),
        status="stable", workflow_revision=1,
        outcome=engine.scheduler._build_work_outcome(run, WORK_ID), created_at=now, updated_at=now,
    ))
    run.conclusion_requirements_ref = system_artifact(
        engine.scheduler.artifact_registry, run, "conclusion_requirements", ConclusionRequirements(),
    )
    engine.scheduler.store.save(run)
    return run


def test_restart_reuses_committed_handoff_and_answer_does_not_reinterpret(tmp_path):
    first_client = BriefClient()
    first = controller(tmp_path, first_client, AskingScientific(crash=True))
    original = stable_run(first)
    with pytest.raises(ProcessCrash):
        first.run_until_stable(RUN_ID)
    saved = first.scheduler.store.load(RUN_ID)
    feedback_ref = saved.feedback_refs[WORK_ID]
    feedback_contents = read_json(feedback_ref)
    assert saved.work_requests[0].status == "stable"  # Prepared is not consumed.
    assert saved.llm_calls_used == 1 and len(first_client.prompts) == 1

    resumed_client, scientific = BriefClient(), AskingScientific()
    resumed = controller(tmp_path, resumed_client, scientific)
    paused = resumed.run_until_stable(RUN_ID)
    request = scientific.requests[0]
    assert request.resume_artifact_ids == [feedback_ref.id]
    assert paused.work_requests[0].status == "consumed"
    assert paused.feedback_refs[WORK_ID] == feedback_ref
    assert read_json(feedback_ref) == feedback_contents
    assert paused.llm_calls_used == saved.llm_calls_used
    assert resumed_client.prompts == []
    assert paused.workflow == original.workflow

    answered_client, answering_scientific = BriefClient(), AskingScientific()
    answering = controller(tmp_path, answered_client, answering_scientific)
    answered = answering.answer_question(RUN_ID, UserAnswer(
        question_id=paused.pending_question.id, values={"metric": "accuracy"}, answered_at=datetime.now(UTC),
    ))
    request = answering_scientific.requests[0]
    resumed_refs = [ref for ref in request.input_artifacts if ref.id in request.resume_artifact_ids]
    assert [ref.kind for ref in resumed_refs] == ["answer"]
    assert answered.research_index_ref in request.input_artifacts
    assert feedback_ref in request.input_artifacts
    assert answered.feedback_refs[WORK_ID] == feedback_ref
    assert answered_client.prompts == [] and answered.llm_calls_used == 1
    assert answered.work_requests[0].status == "consumed"
    assert answered.scientific_observed_artifact_ids == []


def test_last_model_allowance_commits_handoff_before_budget_failure(tmp_path):
    client, scientific = BriefClient(), AskingScientific()
    engine = controller(tmp_path, client, scientific)
    stable_run(engine, max_calls=1)
    failed = engine.run_until_stable(RUN_ID)
    assert failed.status == "failed" and failed.terminal_error.code == "budget_exhausted"
    assert failed.llm_calls_used == 1 and failed.usage.outcomes == {"succeeded": 1, "failed": 0, "unknown": 0}
    assert scientific.requests == [] and len(client.prompts) == 1
    assert failed.work_requests[0].status == "stable"
    feedback = read_json(failed.feedback_refs[WORK_ID], WorkFeedback)
    assert feedback.index_artifact_id == failed.research_index_ref.id
    assert read_json(failed.artifacts[feedback.work_record_artifact_id], WorkRecord).work_request_id == WORK_ID
    assert engine.run_until_stable(RUN_ID) == failed
    assert len(client.prompts) == 1


def test_timeout_after_interpretation_keeps_committed_handoff(tmp_path, monkeypatch):
    from resagent2_orchestrator import controller as module

    class Clock:
        value = datetime.now(UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.value

    client = BriefClient(after_response=lambda: setattr(Clock, "value", Clock.value + timedelta(seconds=61)))
    scientific = AskingScientific()
    engine = controller(tmp_path, client, scientific)
    stable_run(engine, timeout=60)
    monkeypatch.setattr(module, "datetime", Clock)
    failed = engine.run_until_stable(RUN_ID)
    assert failed.status == "failed" and failed.terminal_error.code == "timeout"
    assert failed.llm_calls_used == 1 and scientific.requests == []
    assert WORK_ID in failed.feedback_refs
    assert failed.work_requests[0].status == "stable"


def test_rejected_interpretation_keeps_charges_without_consuming_work(tmp_path):
    client, scientific = BriefClient(invalid=True), AskingScientific()
    engine = controller(tmp_path, client, scientific)
    original = stable_run(engine)
    failed = engine.run_until_stable(RUN_ID)
    assert failed.status == "failed" and failed.terminal_error.code == "contract_error"
    assert "two drafts" in failed.terminal_error.message
    assert len(client.prompts) == failed.llm_calls_used == 2
    assert failed.usage.outcomes == {"succeeded": 2, "failed": 0, "unknown": 0}
    assert failed.work_requests[0].status == "stable"
    assert failed.feedback_refs == {} and failed.research_index_ref is None
    assert failed.workflow == original.workflow and failed.artifacts == original.artifacts
    assert scientific.requests == []


def test_crash_before_handoff_commit_retains_usage_and_rebuilds_once(tmp_path, monkeypatch):
    first_client, scientific = BriefClient(), AskingScientific()
    first = controller(tmp_path, first_client, scientific)
    original = stable_run(first)
    save = first._save

    def crash_before_binding(run):
        if WORK_ID in run.feedback_refs:
            raise ProcessCrash("Lost before the Run handoff binding was committed")
        save(run)

    monkeypatch.setattr(first, "_save", crash_before_binding)
    with pytest.raises(ProcessCrash):
        first.run_until_stable(RUN_ID)
    interrupted = first.scheduler.store.load(RUN_ID)
    assert interrupted.feedback_refs == {} and interrupted.research_index_ref is None
    assert interrupted.llm_calls_used == 1
    assert interrupted.artifacts == original.artifacts
    assert interrupted.work_requests[0].status == "stable" and scientific.requests == []

    next_client = BriefClient()
    resumed = controller(tmp_path, next_client, AskingScientific())
    result = resumed.run_until_stable(RUN_ID)
    assert result.status == "paused" and result.work_requests[0].status == "consumed"
    assert result.llm_calls_used == 2
    assert len(first_client.prompts) == len(next_client.prompts) == 1
    assert len(result.feedback_refs) == 1
    assert len([ref for ref in result.artifacts.values() if ref.kind == "work_record"]) == 1
    assert len([ref for ref in result.artifacts.values() if ref.kind == "work_feedback"]) == 1


def test_historical_indexes_stay_readable_while_current_entry_is_last(tmp_path):
    from resagent2_components import RegisteredArtifactReader
    from resagent2_contracts import ArtifactCandidate
    client = BriefClient()
    engine = controller(tmp_path, client, AskingScientific())
    run = stable_run(engine)
    engine._prepare_research_handoff(run)
    old_index = run.research_index_ref
    historical = engine.scheduler.artifact_registry.register_system_artifact(
        ArtifactCandidate(kind="research_index", path="empty.json", media_type="application/json",
                          summary="Empty historical index", content=json.dumps({"run_id": RUN_ID, "groups": []})),
        run_id=RUN_ID, source_type="research_index",
    )
    run.artifacts[historical.id] = historical
    # Even when an old snapshot appears later in the registry, current pointer wins.
    refs = engine._authorized_artifacts(run)
    assert [ref for ref in refs if ref.kind == "research_index"][-1] == old_index
    for ref in refs:
        if ref.kind == "research_index":
            assert RegisteredArtifactReader(refs, run_id=RUN_ID).read_text(ref.id)["content"]
