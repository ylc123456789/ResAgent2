"""Expose verification outcomes and freshness without deciding task completion."""

from datetime import UTC, datetime
import hashlib
from types import SimpleNamespace

from resagent2_contracts import AgentOwner
from resagent2_runtime import AgentState

from resagent2_coding.completion import derive_control_state
from resagent2_capabilities import GitDiffTool


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


def test_no_edit_or_verification_has_no_pass_result() -> None:
    control = derive_control_state(_state({"edit_revision": 0}), _binding(False))
    assert control["edited_since_verification"] is False
    assert control["verification_stale"] is False
    assert control["verification_passed"] is None
    assert control["verification_issue"] == "No verification executed"
    assert "suggested_next_action" not in control


def test_after_edit_missing_verification_is_distinct_from_stale_results() -> None:
    control = derive_control_state(_state({"edit_revision": 1}), _binding(False))
    assert control["edited_since_verification"] is True
    assert control["verification_stale"] is False
    assert control["verification_passed"] is None
    assert control["environment_certified"] is False
    assert control["verification_issue"] == "No verification executed"


def test_base_audit_does_not_imply_code_verification() -> None:
    # The environment is now audited, but the latest edit is still unverified.
    control = derive_control_state(_state({"edit_revision": 1}), _binding(True))
    assert control["environment_certified"] is True
    assert control["verification_stale"] is False
    assert control["verification_passed"] is None
    assert control["verification_issue"] == "No verification executed"


def test_current_success_reports_pass_and_freshness() -> None:
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
    assert control["verification_issue"] is None
    assert "suggested_next_action" not in control
    assert control["verification_passed"] is True


def test_stored_revision_does_not_imply_verification_results() -> None:
    # A stored revision alone does not prove that verification records exist.
    control = derive_control_state(
        _state({"edit_revision": 2, "verification_revision": 1}), _binding(True)
    )
    assert control["verification_stale"] is False
    assert control["verification_passed"] is None
    assert control["verification_issue"] == "No verification executed"


def test_git_diff_rechecks_current_patch_without_changing_recorded_test_outcome():
    patch = {"value": "original patch"}
    repository = SimpleNamespace(diff_since=lambda baseline: patch["value"])
    binding = _binding(True)
    state = _state({
        "edit_revision": 1, "verification_revision": 1,
        "verification_environment_generation": "generation_test",
        "verification_workspace_unchanged": True,
        "verification_diff_sha256": hashlib.sha256(patch["value"].encode()).hexdigest(),
        "verification_results": [{
            "command": "python -m pytest", "exit_code": 0, "timed_out": False,
            "stdout_path": "test.stdout", "stderr_path": "test.stderr", "duration_seconds": 0,
        }],
    })
    tool = GitDiffTool(repository, baseline=object(),
                       verification_state=lambda state, diff: derive_control_state(state, binding, diff=diff))
    before = tool.execute(state, tool.input_model(max_chars=1)).value
    assert before["truncated"] is True
    assert before["verification"]["verification_passed"] is True
    assert before["verification"]["verification_stale"] is False
    patch["value"] = "changed outside file tools"
    after = tool.execute(state, tool.input_model(max_chars=1)).value
    assert after["verification"]["verification_passed"] is True
    assert after["verification"]["verification_stale"] is True
    assert after["verification"]["verification_issue"] == "Workspace changed after verification"


def test_failed_result_keeps_its_outcome_when_code_or_environment_changes():
    memory = {
        "edit_revision": 1, "verification_revision": 1,
        "verification_environment_generation": "generation_test",
        "verification_workspace_unchanged": True,
        "verification_results": [{
            "command": "python -m unittest", "exit_code": 1, "timed_out": False,
            "stdout_path": "old.stdout", "stderr_path": "old.stderr", "duration_seconds": 1,
        }],
    }
    current = derive_control_state(_state(memory), _binding(True))
    assert current["verification_issue"] is None
    assert "suggested_next_action" not in current
    assert current["verification_passed"] is False
    assert current["verification_stale"] is False
    for change in ({"edit_revision": 2}, {"verification_environment_generation": "old_generation"}):
        result = derive_control_state(_state({**memory, **change}), _binding(True))
        assert result["verification_issue"] is not None
        assert result["verification_passed"] is False
        assert result["verification_stale"] is True
    assert "workspace_changed" not in derive_control_state(_state(memory), _binding(True))
