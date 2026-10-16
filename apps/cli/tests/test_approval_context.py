from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

import pytest

from resagent2_cli.approval_context import approval_context
from resagent2_contracts import (
    ActionSnapshot,
    BusinessInput,
    PendingQuestion,
    RecordedAnswer,
    ResearchRequest,
    RunBudget,
    RunPermissions,
    RunStatus,
)
from resagent2_orchestrator import InMemoryRunStore, ResearchRun


NOW = datetime(2026, 10, 16, 3, 0, tzinfo=UTC)


def _run() -> ResearchRun:
    return ResearchRun(
        run_id="run_review_context",
        request=ResearchRequest(
            goal="Evaluate the frozen model",
            hypothesis="A scalar temperature improves calibration.\n  用户原始假设  ",
            required_artifacts=["metrics.json", "Summary", "metrics.json"],
            required_evidence_kinds=["literature_fulltext", "literature_paper"],
            context="Original context\n  保留原文和空白  \n",
            constraints=["Do not read test before freezing", "Write only in the workspace"],
            budget=RunBudget(max_llm_calls=20, timeout_seconds=120),
            permissions=RunPermissions(execute_commands=True),
        ),
        status=RunStatus.RUNNING,
        created_at=NOW,
        updated_at=NOW,
    )


def _answer(question_id: str, *, minute: int = 1, **changes) -> RecordedAnswer:
    fields = {
        "question_id": question_id,
        "question_text": "Which split should be used?\nOriginal full question.",
        "requested_fields": ["split"],
        "options": {"split": ["calibration", "test"]},
        "values": {"split": "calibration\n  User free text  "},
        "answered_at": NOW + timedelta(minutes=minute),
        "run_id": "run_review_context",
        "session_id": "session_scientific",
    }
    fields.update(changes)
    return RecordedAnswer(**fields)


def test_context_preserves_original_request_and_root_dialogue_in_existing_order():
    run = _run()
    run.upstream_inputs = [
        BusinessInput(event_id="z_input", text="Newer user constraint\n  unchanged  ",
                      source="user", created_at=NOW + timedelta(minutes=2)),
        BusinessInput(event_id="z_same_time", text="An earlier user requirement",
                      source="user", created_at=NOW + timedelta(minutes=1)),
    ]
    run.answers = [_answer("question_a")]
    store = InMemoryRunStore()
    store.save(run)

    result = approval_context(store, run.run_id)

    assert result["initial_request"] == {
        "source": "user", "created_at": NOW.isoformat(),
        "goal": run.request.goal, "hypothesis": run.request.hypothesis,
        "context": run.request.context, "constraints": run.request.constraints,
        "required_artifacts": run.request.required_artifacts,
        "required_evidence_kinds": run.request.required_evidence_kinds,
    }
    dialogue = result["business_dialogue"]
    assert [item["event_id"] for item in dialogue] == ["question_a", "z_same_time", "z_input"]
    assert dialogue[0]["record"] == run.answers[0].model_dump(mode="json", exclude_none=True)
    assert dialogue[2]["record"] == run.upstream_inputs[0].model_dump(mode="json", exclude_none=True)
    assert dialogue[0]["record"]["source"] == "user"
    assert dialogue[0]["record"]["values"]["split"] == "calibration\n  User free text  "
    json.dumps(result, ensure_ascii=False)


def test_context_excludes_approval_parent_interpretation_and_child_copies():
    run = _run()
    action = ActionSnapshot(
        action_id="action_shell", tool="run_shell", arguments={"command": "echo approved"},
        run_id=run.run_id, session_id="session_coding", task_id="task_coding", attempt_number=1,
    )
    run.answers = [
        _answer("question_actual_user"),
        _answer("question_approval", requested_fields=["approve"], options=None,
                values={"approve": "yes"}, action=action, task_id="task_coding", attempt_number=1,
                session_id="session_coding", comment="Approval is not a business requirement"),
        _answer("question_parent", source="parent", source_session_id="session_scientific",
                task_id="task_coding", attempt_number=1, session_id="session_coding",
                values={"split": "Parent interpretation must not become a user instruction"}),
        _answer("question_forwarded", source="user", source_session_id="session_scientific",
                task_id="task_coding", attempt_number=1, session_id="session_coding",
                original_question_id="question_actual_user"),
        _answer("question_child", task_id="task_coding", attempt_number=1,
                session_id="session_coding"),
    ]
    run.upstream_inputs = [
        BusinessInput(event_id="input_user", text="Actual root user input", source="user", created_at=NOW),
        BusinessInput(event_id="input_parent", text="Parent interpretation", source="parent",
                      source_session_id="session_scientific", created_at=NOW),
    ]
    run.task_inputs = {"task_coding": [
        BusinessInput(event_id="input_task_only", text="Task-specific interpretation", source="parent",
                      source_session_id="session_scientific", created_at=NOW),
    ]}
    run.child_questions = {"question_pending_child": PendingQuestion(
        id="question_pending_child", run_id=run.run_id, session_id="session_coding",
        recipient_session_id="session_scientific", task_id="task_coding", attempt_number=1,
        text="A child question is not user authorization", requested_fields=["split"], created_at=NOW,
    )}
    store = InMemoryRunStore()
    store.save(run)

    result = approval_context(store, run.run_id)

    assert [item["event_id"] for item in result["business_dialogue"]] == [
        "input_user", "question_actual_user",
    ]
    encoded = json.dumps(result["business_dialogue"])
    for excluded in ("question_approval", "question_parent", "question_forwarded", "question_child",
                     "question_pending_child", "input_task_only", "input_parent", "action_shell"):
        assert excluded not in encoded


def test_context_does_not_modify_authoritative_records_or_read_other_sources():
    run = _run()

    class RunOnlyStore:
        def __init__(self):
            self.loads = []

        def load(self, run_id):
            self.loads.append(run_id)
            return run

    store = RunOnlyStore()
    before = run.model_dump(mode="json")

    result = approval_context(store, run.run_id)
    result["initial_request"]["constraints"].append("Changed result only")
    result["initial_request"]["required_artifacts"].append("Changed_result_only")
    result["initial_request"]["required_evidence_kinds"].append("literature_paper")

    assert store.loads == [run.run_id]
    assert run.model_dump(mode="json") == before
    assert result["business_dialogue"] == []
    assert result["prior_operation_decisions"] == []


def test_missing_run_is_explicit_instead_of_empty_authorization_context():
    with pytest.raises(KeyError):
        approval_context(InMemoryRunStore(), "run_missing")


def test_historical_operation_decisions_preserve_refusal_and_task_scope_separately():
    run = _run()
    action = ActionSnapshot(
        action_id="action_test", tool="run_shell", arguments={"command": "python evaluate_test.py"},
        context={"cwd": "/workspace/research", "shell": "bash"},
        run_id=run.run_id, session_id="session_experiment", task_id="task_experiment", attempt_number=2,
    )
    denial = _answer(
        "question_denied", minute=2, requested_fields=["approve"], options={"approve": ["yes", "no"]},
        values={"approve": "no"}, action=action, session_id=action.session_id,
        task_id=action.task_id, attempt_number=action.attempt_number,
        comment="Do not evaluate official test before freezing.\n  原始拒绝说明  ",
    )
    approval = _answer(
        "question_approved", minute=1, requested_fields=["approve"], options=None,
        values={"approve": "yes"}, action=action, session_id=action.session_id,
        task_id=action.task_id, attempt_number=action.attempt_number,
        comment="This exact operation only",
    )
    run.answers = [denial, approval, _answer("question_business")]
    store = InMemoryRunStore()
    store.save(run)

    result = approval_context(store, run.run_id)

    history = result["prior_operation_decisions"]
    assert [item["question_id"] for item in history] == ["question_approved", "question_denied"]
    assert [item["decision"] for item in history] == ["yes", "no"]
    assert history[1] == {**denial.model_dump(mode="json", exclude_none=True), "decision": "no"}
    assert history[1]["comment"] == "Do not evaluate official test before freezing.\n  原始拒绝说明  "
    assert history[1]["action"] == action.model_dump(mode="json")
    assert history[1]["source"] == "user"
    assert history[1]["task_id"] == "task_experiment"
    assert [item["event_id"] for item in result["business_dialogue"]] == ["question_business"]


def test_historical_operation_decisions_exclude_parent_and_forwarded_authority():
    run = _run()
    action = ActionSnapshot(
        action_id="action_parent", tool="run_shell", arguments={"command": "echo unsafe"},
        run_id=run.run_id, session_id="session_coding", task_id="task_coding", attempt_number=1,
    )
    common = {
        "requested_fields": ["approve"], "options": None, "values": {"approve": "yes"},
        "action": action, "session_id": action.session_id,
        "task_id": action.task_id, "attempt_number": action.attempt_number,
    }
    run.answers = [
        _answer("question_parent_decision", source="parent", source_session_id="session_scientific", **common),
        _answer("question_forwarded_decision", original_question_id="question_original", **common),
        _answer("question_parent_as_user", source="user", source_session_id="session_scientific", **common),
        _answer("question_parent_as_business_user", source="user", source_session_id="session_scientific"),
    ]
    store = InMemoryRunStore()
    store.save(run)

    result = approval_context(store, run.run_id)

    assert result["prior_operation_decisions"] == []
    assert result["business_dialogue"] == []
