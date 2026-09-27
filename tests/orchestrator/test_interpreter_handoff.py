"""The Controller commits reverse handoffs once and preserves their Run budget."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from resagent2_contracts import (
    AgentOwner, AgentResult, ArtifactCandidate, Attempt,
    ConclusionRequirements, ControlSignal, QuestionDraft, ResearchRequest,
    RunBudget, RunPermissions, SessionRef, UserAnswer, WorkFeedback,
    WorkRecord, WorkRequest, WorkRequestDraft, Workflow, WorkflowAgentDefinition,
    WorkflowAgentRegistry, WorkflowTask,
)
from resagent2_orchestrator import (
    DeterministicWorkInterpreter, JsonRunStore, ResearchController, ResearchRun,
    WorkflowScheduler,
)
from resagent2_orchestrator.handoffs import read_json, system_artifact

RUN_ID = "run_handoff"
WORK_ID = "work_measure"
SESSION_ID = "session_handoff"


class ProcessCrash(BaseException):
    """Fault injection that bypasses normal product error handling."""


class RecordingInterpreter(DeterministicWorkInterpreter):
    def __init__(self, *, invalid=False, after_interpret=None):
        self.records = []
        self.invalid = invalid
        self.after_interpret = after_interpret

    def interpret(self, record):
        self.records.append(record.model_copy(deep=True))
        if self.after_interpret:
            self.after_interpret()
        return "" if self.invalid else super().interpret(record)


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


def controller(tmp_path, interpreter, scientific):
    scheduler = WorkflowScheduler(
        bindings={}, store=JsonRunStore(tmp_path / "state"),
        artifact_root=tmp_path / "artifacts", data_root=tmp_path / "data",
    )
    registry = WorkflowAgentRegistry(definitions=[WorkflowAgentDefinition(
        workflow_agent_kind="experiment", description="Analyze results",
    )])
    return ResearchController(
        scientific_port=scientific, compiler=NoCompiler(), scheduler=scheduler,
        registry=registry, interpreter=interpreter,
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
    first_interpreter = RecordingInterpreter()
    first = controller(tmp_path, first_interpreter, AskingScientific(crash=True))
    original = stable_run(first)
    with pytest.raises(ProcessCrash):
        first.run_until_stable(RUN_ID)
    saved = first.scheduler.store.load(RUN_ID)
    feedback_ref = saved.feedback_refs[WORK_ID]
    feedback_contents = read_json(feedback_ref)
    assert saved.work_requests[0].status == "stable"  # Prepared is not consumed.
    assert saved.llm_calls_used == 0 and len(first_interpreter.records) == 1

    resumed_interpreter, scientific = RecordingInterpreter(), AskingScientific()
    resumed = controller(tmp_path, resumed_interpreter, scientific)
    paused = resumed.run_until_stable(RUN_ID)
    request = scientific.requests[0]
    assert request.resume_artifact_ids == [feedback_ref.id]
    assert paused.work_requests[0].status == "consumed"
    assert paused.feedback_refs[WORK_ID] == feedback_ref
    assert read_json(feedback_ref) == feedback_contents
    assert paused.llm_calls_used == saved.llm_calls_used
    assert resumed_interpreter.records == []
    assert paused.workflow == original.workflow

    answered_interpreter, answering_scientific = RecordingInterpreter(), AskingScientific()
    answering = controller(tmp_path, answered_interpreter, answering_scientific)
    answered = answering.answer_question(RUN_ID, UserAnswer(
        question_id=paused.pending_question.id, values={"metric": "accuracy"}, answered_at=datetime.now(UTC),
    ))
    request = answering_scientific.requests[0]
    resumed_refs = [ref for ref in request.input_artifacts if ref.id in request.resume_artifact_ids]
    assert [ref.kind for ref in resumed_refs] == ["answer"]
    assert answered.research_index_ref in request.input_artifacts
    assert feedback_ref in request.input_artifacts
    assert answered.feedback_refs[WORK_ID] == feedback_ref
    assert answered_interpreter.records == [] and answered.llm_calls_used == 0
    assert answered.work_requests[0].status == "consumed"
    assert answered.scientific_observed_artifact_ids == []


def test_last_model_allowance_is_available_to_scientific_after_fixed_handoff(tmp_path):
    interpreter, scientific = RecordingInterpreter(), AskingScientific()
    engine = controller(tmp_path, interpreter, scientific)
    run = stable_run(engine, max_calls=2)
    run.usage.requests["previous_request:0"] = "unknown"
    engine.scheduler.store.save(run)
    paused = engine.run_until_stable(RUN_ID)
    assert paused.status == "paused" and paused.work_requests[0].status == "consumed"
    assert paused.llm_calls_used == 1
    assert paused.usage.requests == {"previous_request:0": "unknown"}
    assert scientific.requests[0].budget.max_llm_calls == 1
    assert len(interpreter.records) == 1
    feedback = read_json(paused.feedback_refs[WORK_ID], WorkFeedback)
    assert feedback.index_artifact_id == paused.research_index_ref.id
    assert "Measured accuracy" in feedback.report
    assert read_json(paused.artifacts[feedback.work_record_artifact_id], WorkRecord).work_request_id == WORK_ID


def test_timeout_after_interpretation_keeps_committed_handoff(tmp_path, monkeypatch):
    from resagent2_orchestrator import controller as module

    class Clock:
        value = datetime.now(UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.value

    interpreter = RecordingInterpreter(after_interpret=lambda: setattr(Clock, "value", Clock.value + timedelta(seconds=61)))
    scientific = AskingScientific()
    engine = controller(tmp_path, interpreter, scientific)
    stable_run(engine, timeout=60)
    monkeypatch.setattr(module, "datetime", Clock)
    failed = engine.run_until_stable(RUN_ID)
    assert failed.status == "failed" and failed.terminal_error.code == "timeout"
    assert failed.llm_calls_used == 0 and scientific.requests == []
    assert WORK_ID in failed.feedback_refs
    assert failed.work_requests[0].status == "stable"


def test_rejected_report_does_not_consume_work_or_model_budget(tmp_path):
    interpreter, scientific = RecordingInterpreter(invalid=True), AskingScientific()
    engine = controller(tmp_path, interpreter, scientific)
    original = stable_run(engine)
    failed = engine.run_until_stable(RUN_ID)
    assert failed.status == "failed" and failed.terminal_error.code == "contract_error"
    assert "report" in failed.terminal_error.message
    assert len(interpreter.records) == 1 and failed.llm_calls_used == 0
    assert failed.usage.outcomes == {"succeeded": 0, "failed": 0, "unknown": 0}
    assert failed.work_requests[0].status == "stable"
    assert failed.feedback_refs == {} and failed.research_index_ref is None
    assert failed.workflow == original.workflow and failed.artifacts == original.artifacts
    assert scientific.requests == []


def test_crash_before_handoff_commit_rebuilds_once_without_model_usage(tmp_path, monkeypatch):
    first_interpreter, scientific = RecordingInterpreter(), AskingScientific()
    first = controller(tmp_path, first_interpreter, scientific)
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
    assert interrupted.llm_calls_used == 0
    assert interrupted.artifacts == original.artifacts
    assert interrupted.work_requests[0].status == "stable" and scientific.requests == []

    next_interpreter = RecordingInterpreter()
    resumed = controller(tmp_path, next_interpreter, AskingScientific())
    result = resumed.run_until_stable(RUN_ID)
    assert result.status == "paused" and result.work_requests[0].status == "consumed"
    assert result.llm_calls_used == 0
    assert len(first_interpreter.records) == len(next_interpreter.records) == 1
    assert len(result.feedback_refs) == 1
    assert len([ref for ref in result.artifacts.values() if ref.kind == "work_record"]) == 1
    assert len([ref for ref in result.artifacts.values() if ref.kind == "work_feedback"]) == 1


def test_historical_indexes_stay_readable_while_current_entry_is_last(tmp_path):
    from resagent2_components import RegisteredArtifactReader
    from resagent2_contracts import ArtifactCandidate
    interpreter = RecordingInterpreter()
    engine = controller(tmp_path, interpreter, AskingScientific())
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


def test_restart_syncs_new_registered_material_without_regenerating_report(tmp_path):
    from resagent2_components import RegisteredArtifactReader
    from resagent2_components.artifacts import research_artifacts
    from resagent2_contracts import ResearchIndex

    first_interpreter = RecordingInterpreter()
    engine = controller(tmp_path, first_interpreter, AskingScientific(crash=True))
    stable_run(engine)
    with pytest.raises(ProcessCrash):
        engine.run_until_stable(RUN_ID)
    interrupted = engine.scheduler.store.load(RUN_ID)
    original_feedback = interrupted.feedback_refs[WORK_ID]
    original_index = interrupted.research_index_ref
    literature = engine.scheduler.artifact_registry.register_scientific(
        ArtifactCandidate(kind="literature_search", path="search.txt", media_type="text/plain",
                          summary="Literature registered before the process exited", content="A source"),
        run_id=RUN_ID, session_id=SESSION_ID,
    )
    interrupted.artifacts[literature.id] = literature
    engine.scheduler.store.save(interrupted)
    replay_interpreter, scientific = RecordingInterpreter(), AskingScientific()
    resumed = controller(tmp_path, replay_interpreter, scientific)
    paused = resumed.run_until_stable(RUN_ID)
    request = scientific.requests[0]
    index = read_json(paused.research_index_ref, ResearchIndex)
    indexed = {entry.artifact_id for group in index.groups for entry in group.artifacts}
    assert literature.id in indexed
    assert indexed == {ref.id for ref in research_artifacts(request.input_artifacts)}
    assert paused.research_index_ref != original_index
    assert original_index in request.input_artifacts
    assert paused.feedback_refs[WORK_ID] == original_feedback
    assert replay_interpreter.records == [] and paused.llm_calls_used == 0
    reader = RegisteredArtifactReader(request.input_artifacts, run_id=RUN_ID)
    assert reader.read_text(literature.id)["content"] == "A source"
    assert reader.read_text(original_index.id)["content"]


def test_frozen_record_corruption_is_rejected_before_interpreter(tmp_path, monkeypatch):
    from resagent2_orchestrator import controller as module
    from pathlib import Path
    from urllib.parse import urlparse
    from urllib.request import url2pathname

    interpreter, scientific = RecordingInterpreter(), AskingScientific()
    engine = controller(tmp_path, interpreter, scientific)
    stable_run(engine)
    register = module.system_artifact

    def corrupt_record(*args, **kwargs):
        ref = register(*args, **kwargs)
        if ref.kind == "work_record":
            Path(url2pathname(urlparse(ref.uri).path)).write_text("{}")
        return ref

    monkeypatch.setattr(module, "system_artifact", corrupt_record)
    failed = engine.run_until_stable(RUN_ID)
    assert failed.status == "failed" and failed.terminal_error.code == "contract_error"
    assert interpreter.records == [] and scientific.requests == []
    assert failed.feedback_refs == {}
    assert failed.work_requests[0].status == "stable"
    assert failed.llm_calls_used == 0
