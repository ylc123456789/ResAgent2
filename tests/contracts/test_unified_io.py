"""Common Agent boundaries and artifact-owned recovery content."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

import resagent2_contracts as contracts
from resagent2_contracts import (
    AgentOwner, AgentPermissions, AgentRequest, AgentResult, ArtifactCandidate,
    ArtifactRef, ControlSignal, ModuleError, QuestionDraft, RecordedAnswer,
    SessionRef, TaskBudget, TaskProposal, WorkFeedback, WorkRecord, WorkOutcome,
    WorkRequestDraft, WorkTaskOutcome, WorkflowProposal,
)

NOW = datetime.now(UTC)


def request(**changes):
    data = dict(
        agent="coding", run_id="run_test", task_id="task_test", attempt_number=1,
        instruction="Inspect the implementation", budget=TaskBudget(max_llm_calls=5, timeout_seconds=60),
    )
    data.update(changes)
    data.setdefault("permissions", AgentPermissions(execute_commands=True, prepare_environment=True))
    return AgentRequest(**data)


def artifact(**changes):
    data = dict(
        id="artifact_answer", kind="answer", producer="orchestrator", run_id="run_test",
        task_id="task_test", attempt_number=1, uri="file:///tmp/answer.json", sha256="0" * 64,
        media_type="application/json", summary="Reply", metadata={"source_type": "controller_answer"},
    )
    data.update(changes)
    return ArtifactRef(**data)


def session(module="coding"):
    return SessionRef(
        id="session_test", module=module, state_uri="session://session_test",
        status="paused", created_at=NOW, updated_at=NOW,
    )


@pytest.mark.parametrize("agent", ["coding", "experiment", "scientific"])
def test_all_agents_use_the_same_request_and_result(agent):
    context = dict(task_id=None, attempt_number=None) if agent == "scientific" else {}
    req = request(agent=agent, **context)
    assert AgentRequest.model_validate_json(req.model_dump_json()) == req
    result = AgentResult(status="completed", report="Reviewed available materials")
    assert AgentResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("field", [
    "goal", "constraints", "inputs", "dataset_refs", "answers", "facts", "continuation",
    "acceptance", "acceptance_ref", "required_evidence_kinds", "capability", "mode",
])
def test_request_rejects_parallel_task_semantics(field):
    with pytest.raises(ValidationError, match="Extra inputs"):
        request(**{field: []})


def test_request_enforces_invocation_scope_and_dispatch_authority():
    with pytest.raises(ValidationError, match="run-scoped"):
        request(agent="scientific")
    with pytest.raises(ValidationError, match="task_id"):
        request(task_id=None)
    with pytest.raises(ValidationError, match="only Scientific"):
        request(permissions=AgentPermissions(request_work=True, execute_commands=True, prepare_environment=True))
    with pytest.raises(ValidationError):
        request(agent="orchestrator")


def test_run_and_agent_require_explicit_permissions():
    with pytest.raises(ValidationError, match="permissions"):
        AgentRequest.model_validate(request().model_dump(exclude={"permissions"}))
    with pytest.raises(ValidationError, match="permissions"):
        contracts.ResearchRequest(goal="Inspect", budget=contracts.RunBudget(
            max_llm_calls=5, timeout_seconds=60,
        ))
    assert not contracts.RunPermissions().execute_commands
    assert not contracts.RunPermissions().prepare_environment
    assert not AgentPermissions().execute_commands
    assert not AgentPermissions().prepare_environment


def test_resume_ids_are_identity_only_and_must_resolve_to_input_artifacts():
    ref = artifact()
    request(input_artifacts=[ref])  # Historical answers are ordinary first-call material.
    with pytest.raises(ValidationError, match="parent session"):
        request(input_artifacts=[ref], resume_artifact_ids=[ref.id])
    with pytest.raises(ValidationError, match="reference input_artifacts"):
        request(parent_session_id="session_test", resume_artifact_ids=[ref.id])
    request(parent_session_id="session_test", input_artifacts=[ref], resume_artifact_ids=[ref.id])
    feedback = artifact(
        id="artifact_feedback", kind="work_feedback", task_id=None, attempt_number=None,
        session_id="session_test", metadata={"source_type": "controller_feedback"},
    )
    with pytest.raises(ValidationError, match="cannot resume"):
        request(parent_session_id="session_test", input_artifacts=[ref, feedback],
                resume_artifact_ids=[ref.id, feedback.id])


def test_input_materials_reject_cross_run_and_duplicate_identity():
    ref = artifact()
    with pytest.raises(ValidationError, match="unique"):
        request(input_artifacts=[ref, ref])
    with pytest.raises(ValidationError, match="same run"):
        request(input_artifacts=[artifact(run_id="run_other")])


def test_pause_control_resolves_one_returned_candidate_with_correct_kind():
    question = ArtifactCandidate(
        kind="question", path="question.json", media_type="application/json", summary="Choose",
        content=QuestionDraft(text="Which data?", requested_fields=["dataset"]).model_dump_json(),
    )
    result = AgentResult(
        status="needs_user_input", report="Awaiting dataset", session=session(),
        artifacts=[question], control=ControlSignal(action="ask_user", candidate_index=0),
    )
    assert AgentResult.model_validate_json(result.model_dump_json()) == result
    for change in [dict(artifacts=[]), dict(session=None), dict(control=None)]:
        with pytest.raises(ValidationError):
            AgentResult.model_validate(result.model_dump() | change)
    with pytest.raises(ValidationError, match="question artifact"):
        AgentResult.model_validate(result.model_dump() | {"artifacts": [question.model_copy(update={"kind": "metrics"})]})


def test_control_has_one_reference_and_never_embedded_business_content():
    for fields in [{}, {"artifact_id": "artifact_x", "candidate_index": 0}, {"data": {"text": "Question"}}]:
        with pytest.raises(ValidationError):
            ControlSignal(action="ask_user", **fields)


@pytest.mark.parametrize("field", ["payload", "question", "request_work", "opinion", "assessment"])
def test_result_rejects_legacy_business_outputs(field):
    with pytest.raises(ValidationError, match="Extra inputs"):
        AgentResult(status="completed", report="Done", **{field: {}})


def test_failed_result_keeps_report_and_partial_artifacts():
    log = ArtifactCandidate(kind="log", path="run.log", media_type="text/plain", summary="Failure log", content="failed")
    result = AgentResult(status="failed", report="Command failed", artifacts=[log], error=ModuleError(
        code="tool_failed", message="exit 1", retryable=False,
    ))
    assert result.artifacts == [log]


def test_recorded_answer_retains_question_snapshot_and_exact_scope():
    data = dict(
        question_id="question_test", question_text="Which dataset?", requested_fields=["dataset"],
        options={"dataset": ["demo", "full"]}, values={"dataset": "demo"}, answered_at=NOW,
        run_id="run_test", task_id="task_test", attempt_number=2,
    )
    saved = RecordedAnswer(**data)
    assert RecordedAnswer.model_validate_json(saved.model_dump_json()) == saved
    for changes in [dict(question_text=""), dict(attempt_number=None), dict(values={"other": "demo"})]:
        with pytest.raises(ValidationError):
            RecordedAnswer(**(data | changes))


def test_work_record_requires_paired_request_and_matching_outcome():
    data = dict(
        run_id="run_test", session_id="session_test", work_request_id="work_test",
        previous_work_request=WorkRequestDraft(objective="Measure", expected_evidence=["metric"]),
        work_outcome=WorkOutcome(work_request_id="work_test", workflow_revision=1, summary="Done",
            tasks=[WorkTaskOutcome(task_id="task_test", status="completed", summary="Measured")]),
    )
    assert WorkRecord(**data).work_outcome.work_request_id == "work_test"
    for missing in ["previous_work_request", "work_outcome"]:
        with pytest.raises(ValidationError):
            WorkRecord(**{key: value for key, value in data.items() if key != missing})
    with pytest.raises(ValidationError, match="must match"):
        WorkRecord(**(data | {"work_request_id": "work_other"}))


def test_graph_rejects_scientific_and_unknown_named_output():
    fields = dict(id="task_first", work_request_id="work_test", instruction="Measure")
    with pytest.raises(ValidationError):
        TaskProposal(workflow_agent_kind="scientific", **fields)
    first = TaskProposal(workflow_agent_kind="experiment", output_names=["metrics"], **fields)
    second = TaskProposal(
        id="task_second", work_request_id="work_test", workflow_agent_kind="coding", instruction="Read",
        depends_on=[first.id], input_artifact_bindings=[dict(source_task=first.id, output_selector="missing")],
    )
    with pytest.raises(ValidationError, match="undeclared"):
        WorkflowProposal(work_request_id="work_test", tasks=[first, second])


def test_contract_revalidation_rejects_unchecked_model_copy():
    invalid_request = request().model_copy(update={"agent": "scientific"})
    with pytest.raises(ValidationError, match="run-scoped"):
        AgentRequest.model_validate(invalid_request)
    task = TaskProposal(
        id="task_test", work_request_id="work_test", workflow_agent_kind="coding", instruction="Inspect",
    ).model_copy(update={"workflow_agent_kind": "scientific"})
    with pytest.raises(ValidationError):
        WorkflowProposal(work_request_id="work_test", tasks=[task])


def test_replaced_contracts_are_not_exported_or_aliased():
    for name in ["Capability", "CapabilityInput", "ModuleTaskRequest", "ModuleResult",
                 "CodeUnderstandInput", "CodeModifyResult", "ExperimentResult", "ScientificTurnRequest", "ScientificTurnResult"]:
        assert not hasattr(contracts, name)


@pytest.mark.parametrize("kind", sorted(contracts.SYSTEM_ARTIFACT_KINDS))
def test_system_kind_policy_rejects_wrong_producer_and_source(kind):
    source, scopes = contracts.SYSTEM_ARTIFACT_PROVENANCE[kind]
    for scope in scopes:
        fields = {
            "run": {}, "task": {"task_id": "task_test"},
            "attempt": {"task_id": "task_test", "attempt_number": 1},
            "session": {"session_id": "session_test"},
        }[scope]
        data = {"kind": kind, "task_id": None, "attempt_number": None,
                "metadata": {"source_type": source}} | fields
        ref = artifact(**data)
        assert ref.producer == AgentOwner.ORCHESTRATOR
        with pytest.raises(ValidationError, match="orchestrator producer"):
            ArtifactRef.model_validate(ref.model_dump() | {"producer": "coding"})
        with pytest.raises(ValidationError, match="source_type"):
            ArtifactRef.model_validate(ref.model_dump() | {"metadata": {"source_type": "import"}})


def test_unknown_scientific_session_kind_is_rejected():
    with pytest.raises(ValidationError, match="unsupported scientific"):
        artifact(kind="unknown_kind", producer="scientific", task_id=None, attempt_number=None,
                 session_id="session_test", metadata={})
