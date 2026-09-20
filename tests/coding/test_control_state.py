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
        agent_name="coding",
        owner=AgentOwner.CODING,
        run_id="run_test",
        task_id="task_test",
        attempt_number=1,
        created_at=now,
        updated_at=now,
        memory=memory,
    )


def _binding(certified: bool):
    return SimpleNamespace(certified=certified, generation="generation_test")


def test_no_edit_yet_is_not_unverified() -> None:
    control = derive_control_state(_state({"edit_revision": 0}), _binding(False))
    assert control["edited_since_verification"] is False
    assert control["verification_stale"] is False
    assert control["suggested_next_action"] == "none"


def test_after_edit_verification_is_required() -> None:
    control = derive_control_state(_state({"edit_revision": 1}), _binding(False))
    assert control["edited_since_verification"] is True
    assert control["verification_stale"] is True
    assert control["environment_certified"] is False
    assert control["suggested_next_action"] == "audit_env"


def test_after_audit_still_requires_verification() -> None:
    # The environment is now audited, but the latest edit is still unverified.
    control = derive_control_state(_state({"edit_revision": 1}), _binding(True))
    assert control["environment_certified"] is True
    assert control["verification_stale"] is True
    assert control["suggested_next_action"] == "run_verification"


def test_after_verification_obligation_clears() -> None:
    control = derive_control_state(
        _state({
            "edit_revision": 1,
            "verification_revision": 1,
            "verification_environment_generation": "generation_test",
            "verification_workspace_unchanged": True,
            "verification_results": [{
                "command": "python -m pytest", "exit_code": 0, "timed_out": False,
                "stdout_path": "test.stdout", "stderr_path": "test.stderr",
                "duration_seconds": 0.0,
            }],
        }), _binding(True)
    )
    assert control["edited_since_verification"] is False
    assert control["verification_stale"] is False
    assert control["suggested_next_action"] == "finish"


def test_newer_edit_reopens_verification() -> None:
    # A second edit after a verification re-opens the obligation.
    control = derive_control_state(
        _state({"edit_revision": 2, "verification_revision": 1}), _binding(True)
    )
    assert control["verification_stale"] is True
    assert control["suggested_next_action"] == "run_verification"


def test_old_failure_does_not_override_a_new_revision_or_environment():
    memory = {
        "edit_revision": 1, "verification_revision": 1,
        "verification_environment_generation": "generation_test",
        "verification_workspace_unchanged": True,
        "verification_results": [{
            "command": "python -m unittest", "exit_code": 1, "timed_out": False,
            "stdout_path": "old.stdout", "stderr_path": "old.stderr", "duration_seconds": 1,
        }],
    }
    assert derive_control_state(_state(memory), _binding(True))["suggested_next_action"] == "inspect_and_fix_verification"
    for change in ({"edit_revision": 2}, {"verification_environment_generation": "old_generation"}):
        result = derive_control_state(_state({**memory, **change}), _binding(True))
        assert result["suggested_next_action"] == "run_verification"
    assert "workspace_changed" not in derive_control_state(_state(memory), _binding(True))
