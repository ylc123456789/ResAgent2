"""Deterministic "modify—verify" control state for the Coding Agent."""

from datetime import UTC, datetime
from types import SimpleNamespace

from resagent2_contracts import AgentOwner
from resagent2_runtime import AgentState

from resagent2_coding.completion import derive_control_state


def _state(memory: dict) -> AgentState:
    now = datetime.now(UTC)
    return AgentState(
        session_id="session_test",
        agent_name="coding-modify",
        owner=AgentOwner.CODING,
        run_id="run_test",
        task_id="task_test",
        attempt_number=1,
        created_at=now,
        updated_at=now,
        memory=memory,
    )


def _binding(certified: bool):
    return SimpleNamespace(certified=certified)


def test_no_edit_yet_is_not_unverified() -> None:
    control = derive_control_state(_state({"edit_revision": 0}), _binding(False))
    assert control["workspace_changed"] is False
    assert control["verification_required"] is False
    assert control["required_next_action"] == "make_the_required_change"


def test_after_edit_verification_is_required() -> None:
    control = derive_control_state(_state({"edit_revision": 1}), _binding(False))
    assert control["workspace_changed"] is True
    assert control["verification_required"] is True
    assert control["environment_certified"] is False
    assert control["required_next_action"] == "audit_env"


def test_after_audit_still_requires_verification() -> None:
    # The environment is now audited, but the latest edit is still unverified.
    control = derive_control_state(_state({"edit_revision": 1}), _binding(True))
    assert control["environment_certified"] is True
    assert control["verification_required"] is True
    assert control["required_next_action"] == "run_verification"


def test_after_verification_obligation_clears() -> None:
    control = derive_control_state(
        _state({"edit_revision": 1, "verification_revision": 1}), _binding(True)
    )
    assert control["workspace_changed"] is False
    assert control["verification_required"] is False
    assert control["required_next_action"] == "make_the_required_change"


def test_newer_edit_reopens_verification() -> None:
    # A second edit after a verification re-opens the obligation.
    control = derive_control_state(
        _state({"edit_revision": 2, "verification_revision": 1}), _binding(True)
    )
    assert control["verification_required"] is True
    assert control["required_next_action"] == "run_verification"
