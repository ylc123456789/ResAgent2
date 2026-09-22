"""Scientific uses the common result while retaining evidence and recovery rules."""

import hashlib
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from resagent2_contracts import (
    AgentOwner, AgentPermissions, AgentRequest, ArtifactRef, ErrorCode,
    ScientificOpinion, TaskBudget, WorkFeedback, WorkOutcome, WorkRequestDraft,
    WorkTaskOutcome, scientific_session_id,
)
from resagent2_scientific import ScientificAgent
from resagent2_scientific.tools import RequestWorkTool
from resagent2_runtime import AgentState, ScriptedLLMClient, ToolRegistry


def request(*, artifacts=(), parent=None, resume=(), budget=10, request_work=True):
    return AgentRequest(run_id='run_example', agent=AgentOwner.SCIENTIFIC, instruction='Evaluate the method', input_artifacts=list(artifacts), parent_session_id=parent, resume_artifact_ids=list(resume), budget=TaskBudget(max_llm_calls=budget, timeout_seconds=60), permissions=AgentPermissions(request_work=request_work, execute_commands=True, prepare_environment=True))


def artifact(root, artifact_id="artifact_1", *, kind="experiment_result", content=None, session=None):
    content = json.dumps({"value": 1} if content is None else content)
    path = root / (artifact_id + ".json")
    path.write_text(content)
    kwargs = dict(
        id=artifact_id, kind=kind, run_id="run_example", uri=path.as_uri(),
        sha256=hashlib.sha256(content.encode()).hexdigest(), media_type="application/json", summary=kind,
    )
    if kind == "experiment_result":
        kwargs.update(producer=AgentOwner.EXPERIMENT, task_id="task_exp", attempt_number=1)
    else:
        sources = {"conclusion_requirements": "conclusion_requirement",
                   "work_feedback": "controller_feedback", "answer": "controller_answer"}
        kwargs.update(producer=AgentOwner.ORCHESTRATOR, metadata={"source_type": sources.get(kind, "import")})
        if session:
            kwargs["session_id"] = session
    return ArtifactRef(**kwargs)


def finish(*, evidence=(), verdict="inconclusive", **opinion):
    body = {"verdict": verdict, "statement": "A statement",
            "evidence_artifact_ids": list(evidence), **opinion}
    return {"tool": "finish", "arguments": {
        "report": "Scientific conclusion is ready",
        "artifacts": [{"kind": "scientific_opinion", "path": "scientific_opinion.json",
                       "media_type": "application/json", "summary": "Scientific judgment",
                       "content": json.dumps(body)}],
    }}


def content(result, kind):
    item = next(item for item in result.artifacts if item.kind == kind)
    return json.loads(item.content)


def work():
    return {"tool": "request_work", "arguments": {
        "assessment": {"statement": "Need more evidence"},
        "work_request": {"objective": "Run the experiment", "expected_evidence": ["accuracy"]},
    }}


def ask():
    return {"tool": "ask_user", "arguments": {
        "assessment": {"statement": "Need a dataset choice"},
        "text": "No dataset is selected. Which dataset should we use?", "requested_fields": ["dataset"],
    }}


def feedback(root, session, *, unresolved=()):
    draft = WorkRequestDraft(objective="Run experiment", expected_evidence=["accuracy"])
    value = WorkFeedback(
        run_id="run_example", work_request_id="work_round1", session_id=session,
        previous_work_request=draft,
        work_outcome=WorkOutcome(
            work_request_id="work_round1", workflow_revision=1, summary="Experiment finished",
            tasks=[WorkTaskOutcome(task_id="task_exp", status="completed", summary="Ran")],
        ),
        unresolved_task_outcomes=list(unresolved),
    )
    return artifact(root, "artifact_feedback", kind="work_feedback", session=session,
                    content=value.model_dump(mode="json"))


def test_work_feedback_requires_paired_request():
    with pytest.raises(ValidationError):
        WorkFeedback.model_validate({
            "run_id": "run_example", "work_request_id": "work_one",
            "session_id": "session_one",
            "work_outcome": {"work_request_id": "work_one", "workflow_revision": 1,
                             "summary": "Done", "tasks": []},
        })


def test_finish_with_existing_evidence_completes(tmp_path):
    ref = artifact(tmp_path)
    agent = ScientificAgent(ScriptedLLMClient([
        {"tool": "read_artifact", "arguments": {"artifact_id": ref.id}},
        finish(evidence=[ref.id], verdict="supports"),
    ]))
    result = agent.invoke(request(artifacts=[ref]))
    assert result.status == "completed", result.report
    assert content(result, "scientific_opinion")["verdict"] == "supports"
    assert content(result, "observation_trace")["observed_artifact_ids"] == [ref.id]


def test_request_work_has_only_artifact_control_content():
    result = ScientificAgent(ScriptedLLMClient([work()])).invoke(request())
    assert result.status == "request_work", result.report
    assert result.control.action == "request_work"
    assert result.control.candidate_index == 0
    assert content(result, "work_request")["work_request"]["expected_evidence"] == ["accuracy"]
    assert result.report == "Need more evidence"


def test_request_work_permission_is_enforced():
    result = ScientificAgent(ScriptedLLMClient([work()])).invoke(request(request_work=False))
    assert result.status == "failed"


def test_empty_expected_evidence_rejected_before_tool_execution(monkeypatch):
    tool = RequestWorkTool()
    monkeypatch.setattr(tool, "execute", lambda *_: pytest.fail("Invalid arguments executed"))
    now = datetime.now(UTC)
    state = AgentState(session_id="session_v", agent_name="scientific",
                       owner=AgentOwner.SCIENTIFIC, run_id="run_example", created_at=now, updated_at=now)
    action = work()["arguments"]
    action["work_request"]["expected_evidence"] = []
    with pytest.raises(ValidationError):
        ToolRegistry((tool,)).dispatch("request_work", action, state)


def test_imported_literature_can_satisfy_requirements_after_reading(tmp_path):
    ref = artifact(tmp_path, kind="literature_search")
    requirements = artifact(tmp_path, "artifact_requirements", kind="conclusion_requirements",
                            content={"required_evidence_kinds": ["literature_search"]})
    client = ScriptedLLMClient([
        {"tool": "read_artifact", "arguments": {"artifact_id": ref.id}},
        finish(evidence=[ref.id], verdict="supports"),
    ])
    result = ScientificAgent(client).invoke(request(artifacts=[ref, requirements]))
    assert result.status == "completed", result.report
    assert content(result, "observation_trace")["observed_artifact_ids"] == [ref.id]
    assert result.llm_calls == 2
    assert "required_evidence_kinds" in client.contexts[0].text


def test_literature_search_ref_is_returned_without_duplicate_registration(tmp_path):
    from resagent2_components import LiteraturePaper

    class Backend:
        def search(self, query, *, max_results, start_year=None, end_year=None):
            return [LiteraturePaper(paper_id="123", title="Paper", authors=["Author"],
                                    abstract="Result", source_url="https://example.com/paper")]

    class Register:
        calls = 0
        ref = None

        def register_scientific(self, candidate, *, run_id, session_id):
            self.calls += 1
            path = tmp_path / "literature.md"
            path.write_text(candidate.content)
            self.ref = ArtifactRef(
                id="artifact_lit", kind="literature_search", producer=AgentOwner.SCIENTIFIC,
                run_id=run_id, session_id=session_id, uri=path.as_uri(),
                sha256=hashlib.sha256(candidate.content.encode()).hexdigest(),
                media_type="text/markdown", summary="Literature",
            )
            return self.ref

        def resolve(self, artifact_id, *, run_id):
            return self.ref if self.ref and self.ref.id == artifact_id else None

    register = Register()
    result = ScientificAgent(ScriptedLLMClient([
        {"tool": "literature_search", "arguments": {"query": "method"}},
        finish(evidence=["artifact_lit"]),
    ]), literature_backend=Backend(), registration_port=register).invoke(request())
    assert result.status == "completed", result.report
    assert register.calls == 1
    assert register.ref in result.artifacts
    assert content(result, "observation_trace")["observed_artifact_ids"] == ["artifact_lit"]


def test_question_pauses_with_content_reference():
    result = ScientificAgent(ScriptedLLMClient([ask()])).invoke(request())
    assert result.status == "needs_user_input"
    assert result.control.action == "ask_user"
    assert content(result, "question")["requested_fields"] == ["dataset"]
    assert result.report == "Need a dataset choice"


def test_question_schema_retains_visible_background_guidance():
    from resagent2_scientific.models import AskUserInput
    from resagent2_runtime.tools import AskUserToolInput
    assert AskUserInput.model_fields["text"].description == AskUserToolInput.model_fields["text"].description
    assert "background" in AskUserInput.model_fields["text"].description


def test_feedback_resume_reuses_session_and_is_idempotent(tmp_path):
    client = ScriptedLLMClient([work(), finish(), finish(verdict="supports", evidence=["artifact_wrong"])])
    agent = ScientificAgent(client)
    first = agent.invoke(request())
    ref = feedback(tmp_path, first.session.id)
    resumed = request(artifacts=[ref], parent=first.session.id, resume=[ref.id])
    result = agent.invoke(resumed)
    duplicate = agent.invoke(resumed)
    assert result.status == duplicate.status == "completed"
    assert result.session.id == first.session.id
    assert content(result, "scientific_opinion") == content(duplicate, "scientific_opinion")
    assert duplicate.llm_calls == 0
    assert len(client.contexts) == 2


def test_answer_resume_projects_registered_content(tmp_path):
    client = ScriptedLLMClient([ask(), finish()])
    agent = ScientificAgent(client)
    first = agent.invoke(request())
    answer = artifact(tmp_path, "artifact_answer", kind="answer", session=first.session.id,
                      content={"question_id": "question_one", "values": {"dataset": "CIFAR-10"},
                               "question_text": "Which dataset?", "requested_fields": ["dataset"],
                               "run_id": "run_example", "session_id": first.session.id,
                               "answered_at": datetime.now(UTC).isoformat()})
    result = agent.invoke(request(artifacts=[answer], parent=first.session.id, resume=[answer.id]))
    assert result.status == "completed", result.report
    assert "CIFAR-10" in client.contexts[-1].text


def test_unread_citation_is_rejected(tmp_path):
    result = ScientificAgent(ScriptedLLMClient([finish(evidence=["artifact_unread"], verdict="supports")])).invoke(
        request(artifacts=[artifact(tmp_path)]),
    )
    assert result.status == "failed"
    assert result.error.code == ErrorCode.TOOL_FAILED


def test_unread_citation_can_be_recovered_by_reading(tmp_path):
    ref = artifact(tmp_path)
    agent = ScientificAgent(ScriptedLLMClient([
        finish(evidence=[ref.id], verdict="supports"),
        {"tool": "read_artifact", "arguments": {"artifact_id": ref.id}},
        finish(evidence=[ref.id], verdict="supports"),
    ]))
    result = agent.invoke(request(artifacts=[ref]))
    assert result.status == "completed"


def test_failed_work_requires_limitation_in_opinion(tmp_path):
    agent = ScientificAgent(ScriptedLLMClient([work(), finish()]))
    first = agent.invoke(request())
    from resagent2_contracts import ModuleError
    unresolved = WorkTaskOutcome(
        task_id="task_failed", status="failed", summary="Crashed",
        error=ModuleError(code=ErrorCode.TOOL_FAILED, message="Crashed", retryable=False),
    )
    ref = feedback(tmp_path, first.session.id, unresolved=[unresolved])
    result = agent.invoke(request(artifacts=[ref], parent=first.session.id, resume=[ref.id]))
    assert result.status == "failed"


def test_budget_exhaustion_counts_real_calls(tmp_path):
    ref = artifact(tmp_path)
    result = ScientificAgent(ScriptedLLMClient([
        {"tool": "read_artifact", "arguments": {"artifact_id": ref.id}},
    ] * 4)).invoke(request(artifacts=[ref], budget=2))
    assert result.status == "failed"
    assert result.error.code == ErrorCode.BUDGET_EXHAUSTED
    assert result.llm_calls == 2


@pytest.mark.parametrize("tool", ["ask_user", "request_work"])
def test_assessment_cannot_cite_unobserved_evidence(tool):
    action = ask() if tool == "ask_user" else work()
    action["arguments"]["assessment"]["evidence_artifact_ids"] = ["artifact_unread"]
    result = ScientificAgent(ScriptedLLMClient([action])).invoke(request())
    assert result.status == "failed"


def test_repeated_first_request_uses_persisted_result():
    client = ScriptedLLMClient([work(), finish()])
    agent = ScientificAgent(client)
    first = agent.invoke(request())
    duplicate = agent.invoke(request())
    assert first.status == duplicate.status == "request_work"
    assert content(first, "work_request") == content(duplicate, "work_request")
    assert duplicate.llm_calls == 0
    assert len(client.contexts) == 1


def test_earlier_reads_remain_in_context(tmp_path):
    a = artifact(tmp_path, "artifact_a")
    b = artifact(tmp_path, "artifact_b")
    client = ScriptedLLMClient([
        {"tool": "read_artifact", "arguments": {"artifact_id": a.id}},
        {"tool": "read_artifact", "arguments": {"artifact_id": b.id}}, finish(evidence=[a.id, b.id]),
    ])
    result = ScientificAgent(client).invoke(request(artifacts=[a, b]))
    assert result.status == "completed"
    assert "artifact_reads" in client.contexts[-1].included_sections
    assert content(result, "observation_trace")["observed_artifact_ids"] == [a.id, b.id]


def test_obsolete_opinion_fields_are_rejected():
    result = ScientificAgent(ScriptedLLMClient([
        finish(acknowledged_task_ids=["task_unknown"]),
    ])).invoke(request())
    assert result.status == "failed"
