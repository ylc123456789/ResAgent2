"""Business dialogue is complete while authorization remains operation-local."""
from datetime import UTC,datetime
import pytest
from pydantic import ValidationError
from resagent2_contracts import AgentRequest,AgentPermissions,TaskBudget,RecordedAnswer,ActionSnapshot
from resagent2_components import request_task_context


def request(**kw):
    return AgentRequest(run_id="run_test", agent="scientific", instruction="Original requirement",
        budget=TaskBudget(max_llm_calls=8, timeout_seconds=60), permissions=AgentPermissions(), **kw)


def answer(n):
    return RecordedAnswer(question_id=f"question_{n}", question_text=f"Full question {n}",
        requested_fields=["scope"], options={"scope":["full","small"]}, values={"scope":"full"},
        run_id="run_test", session_id="session_scientific_run_test", answered_at=datetime.now(UTC))


def test_task_context_preserves_all_business_question_fields_and_initial_facts():
    req=request(parent_session_id="session_scientific_run_test", user_answers=[answer(1),answer(2)],
                task_context={"required_artifacts":["comparison_results"]})
    before=req.model_dump_json()
    value=request_task_context(req)
    assert value["instruction"]=="Original requirement"
    assert value["task_context"]["required_artifacts"]==["comparison_results"]
    assert [item["question_id"] for item in [event["record"] for event in value["business_dialogue"] if event["type"] == "answer"]]==["question_1","question_2"]
    assert [event["record"] for event in value["business_dialogue"] if event["type"] == "answer"][0]["requested_fields"]==["scope"]
    assert [event["record"] for event in value["business_dialogue"] if event["type"] == "answer"][0]["options"]=={"scope":["full","small"]}
    assert req.model_dump_json()==before


def test_resume_answer_is_not_silently_presented_as_a_business_requirement():
    req=request(parent_session_id="session_scientific_run_test",resume_answers=[answer(1)])
    assert [e["record"] for e in request_task_context(req)["business_dialogue"] if e["type"] == "answer"]==[]


def test_business_answers_reject_foreign_scope():
    with pytest.raises(ValidationError):
        request(parent_session_id="session_scientific_run_test",user_answers=[answer(1).model_copy(update={"run_id":"run_foreign"})])
