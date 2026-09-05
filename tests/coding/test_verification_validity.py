"""One environment-generation validity rule drives guidance and completion."""

from datetime import UTC, datetime
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from resagent2_capabilities import (
    AuditEnvTool, EnvironmentBinding, EnvironmentManagerError, PreparedEnvironment,
    PrepareEnvironmentTool, RunSetupTool, RunVerificationTool, WorkspaceBoundary,
)
from resagent2_contracts import (
    AgentOwner, VerificationResult, WorkspaceGrant, WorkspaceMode, WorkspaceSourceKind,
)
from resagent2_coding.completion import CodeModifyCompletionCheck, derive_control_state
from resagent2_runtime import AgentState, FinishCandidate


DIFF = "diff --git a/code.py b/code.py\n-old\n+new\n"


class Manager:
    conda_exe = "conda"

    def __init__(self, root):
        self.environment = PreparedEnvironment(
            env_id="resenv_test", prefix=root / "env", python_version="3.12"
        )
        self.prepare_error = None

    def inspect(self, **kwargs):
        return self.environment

    def prepare(self, **kwargs):
        if self.prepare_error is not None:
            raise self.prepare_error
        return self.environment

    def audit(self, environment):
        return {"success": True}


class Runner:
    def __init__(self, boundary, *, exit_code=0, error=None):
        self.boundary = boundary
        self.exit_code = exit_code
        self.error = error

    def run(self, command, **kwargs):
        if self.error is not None:
            raise self.error
        return VerificationResult(
            command=command, exit_code=self.exit_code, timed_out=False,
            stdout_path=str(self.boundary.root / "stdout.log"),
            stderr_path=str(self.boundary.root / "stderr.log"), duration_seconds=0.0,
        )


@pytest.fixture
def setup(tmp_path):
    (tmp_path / "code.py").write_text("new\n", encoding="utf-8")
    boundary = WorkspaceBoundary(WorkspaceGrant(
        root=str(tmp_path), mode=WorkspaceMode.READ_WRITE,
        allowed_paths=["."], source=WorkspaceSourceKind.LOCAL,
    ))
    manager = Manager(tmp_path)
    binding = EnvironmentBinding(manager, run_id="run_test", workspace_id="ws_test")
    binding.certified = True
    repository = SimpleNamespace(
        diff_since=lambda baseline: DIFF,
        changed_paths_since=lambda baseline: ["code.py"],
        deleted_paths_since=lambda baseline: [],
    )
    baseline = object()
    check = CodeModifyCompletionCheck(
        repository, boundary, output_root=str(tmp_path / "output"),
        baseline=baseline, env_binding=binding,
    )
    now = datetime.now(UTC)
    result = Runner(boundary).run("python -m pytest")
    state = AgentState(
        session_id="session_test", agent_name="coding-modify", owner=AgentOwner.CODING,
        run_id="run_test", task_id="task_test", attempt_number=1,
        created_at=now, updated_at=now,
        memory={
            "edit_revision": 1, "verification_revision": 1,
            "verification_results": [result.model_dump(mode="json")],
            "verification_environment_generation": binding.generation,
            "verification_workspace_unchanged": True,
            "verification_diff_sha256": hashlib.sha256(DIFF.encode()).hexdigest(),
        },
    )
    return SimpleNamespace(root=tmp_path, binding=binding, manager=manager,
                           boundary=boundary, repository=repository, baseline=baseline,
                           check=check, state=state)


def finish(check, state):
    return check.evaluate(state, FinishCandidate(result={"summary": "done"}))


def reaudit(binding, state):
    tool = AuditEnvTool(binding)
    return tool.execute(state, tool.input_model())


def reverify(setup, *, exit_code=0):
    tool = RunVerificationTool(
        Runner(setup.boundary, exit_code=exit_code), setup.repository,
        log_root=str(setup.root / "verify"), timeout_seconds=30,
        baseline=setup.baseline, env_binding=setup.binding,
    )
    observation = tool.execute(
        setup.state, tool.input_model(commands=["python -m pytest"])
    )
    setup.state.memory.update(observation.memory_updates)
    return observation


@pytest.mark.parametrize("exit_code", [0, 1])
def test_setup_then_audit_cannot_revive_prior_verification(setup, exit_code):
    assert finish(setup.check, setup.state).complete
    generation = setup.binding.generation
    tool = RunSetupTool(
        Runner(setup.boundary, exit_code=exit_code), setup.binding,
        log_dir=str(setup.root / "setup"), timeout_seconds=30,
    )

    observation = tool.execute(setup.state, tool.input_model(command="pip install numpy"))

    assert observation.ok is (exit_code == 0)
    assert setup.binding.generation != generation
    assert setup.binding.certified is False
    assert not finish(setup.check, setup.state).complete
    assert derive_control_state(setup.state, setup.binding)["required_next_action"] == "audit_env"
    generation = setup.binding.generation
    assert reaudit(setup.binding, setup.state).ok
    assert setup.binding.generation == generation
    assert not finish(setup.check, setup.state).complete
    assert derive_control_state(setup.state, setup.binding)["required_next_action"] == "run_verification"
    assert reverify(setup).ok
    assert setup.state.memory["verification_environment_generation"] == generation
    assert finish(setup.check, setup.state).complete
    assert derive_control_state(setup.state, setup.binding)["required_next_action"] == "finish"


def test_setup_exception_invalidates_before_runner_raises(setup):
    generation = setup.binding.generation
    tool = RunSetupTool(
        Runner(setup.boundary, error=RuntimeError("runner interrupted")), setup.binding,
        log_dir=str(setup.root / "setup"), timeout_seconds=30,
    )

    with pytest.raises(RuntimeError, match="runner interrupted"):
        tool.execute(setup.state, tool.input_model(command="pip install numpy"))

    assert setup.binding.generation != generation
    assert not setup.binding.certified
    assert reaudit(setup.binding, setup.state).ok
    assert not finish(setup.check, setup.state).complete


@pytest.mark.parametrize("error", [None, EnvironmentManagerError("failed"), RuntimeError("interrupted")])
def test_actual_prepare_invalidates_even_on_failure(setup, error):
    setup.manager.prepare_error = error
    generation = setup.binding.generation
    tool = PrepareEnvironmentTool(setup.binding)

    if isinstance(error, RuntimeError) and not isinstance(error, EnvironmentManagerError):
        with pytest.raises(RuntimeError, match="interrupted"):
            tool.execute(setup.state, tool.input_model(python_version="3.12"))
    else:
        observation = tool.execute(setup.state, tool.input_model(python_version="3.12"))
        assert observation.ok is (error is None)

    assert setup.binding.generation != generation
    assert not setup.binding.certified
    assert not finish(setup.check, setup.state).complete


@pytest.mark.parametrize("kind", ["policy", "version", "constraint", "switch_limit"])
def test_rejected_setup_or_prepare_does_not_invalidate(setup, kind):
    generation = setup.binding.generation
    if kind == "policy":
        tool = RunSetupTool(Runner(setup.boundary), setup.binding,
                            log_dir=str(setup.root / "setup"), timeout_seconds=30)
        observation = tool.execute(setup.state, tool.input_model(command="sudo pip install x"))
    else:
        tool = PrepareEnvironmentTool(setup.binding)
        version = "invalid" if kind == "version" else "3.11"
        if kind == "constraint":
            setup.binding.hard_constraint = "3.12"
        if kind == "switch_limit":
            setup.state.memory.update(last_requested_python="3.12", version_switches=2)
        observation = tool.execute(setup.state, tool.input_model(python_version=version))

    assert observation.ok is False
    assert setup.binding.generation == generation
    assert setup.binding.certified
    assert finish(setup.check, setup.state).complete


def test_new_binding_and_reaudit_require_new_verification(setup):
    previous = setup.binding.generation
    restored = EnvironmentBinding(setup.manager, run_id="run_test", workspace_id="ws_test")
    assert restored.generation != previous
    assert not restored.certified
    setup.binding = restored
    setup.check.env_binding = restored
    assert reaudit(restored, setup.state).ok

    assert not finish(setup.check, setup.state).complete
    assert derive_control_state(setup.state, restored)["required_next_action"] == "run_verification"
    assert reverify(setup).ok
    assert finish(setup.check, setup.state).complete


@pytest.mark.parametrize("exit_code", [1, 2])
def test_failed_verification_never_guides_finish(setup, exit_code):
    observation = reverify(setup, exit_code=exit_code)

    assert observation.ok is False
    assert setup.state.memory["verification_revision"] == setup.state.memory["edit_revision"]
    control = derive_control_state(setup.state, setup.binding)
    assert control["verification_required"]
    assert control["required_next_action"] == "inspect_and_fix_verification"
    assert not finish(setup.check, setup.state).complete


@pytest.mark.parametrize("results", [[], None, ["invalid"], [{"exit_code": 0}]])
def test_missing_or_invalid_results_cannot_satisfy_verification(setup, results):
    setup.state.memory["verification_results"] = results

    assert derive_control_state(setup.state, setup.binding)["required_next_action"] == "run_verification"
    decision = finish(setup.check, setup.state)
    assert not decision.complete
    assert decision.summary == derive_control_state(setup.state, setup.binding)["verification_issue"]


def test_latest_edit_and_workspace_digest_still_enforced(setup):
    setup.state.memory["edit_revision"] = 2
    assert not finish(setup.check, setup.state).complete
    assert derive_control_state(setup.state, setup.binding)["verification_required"]
    setup.state.memory["edit_revision"] = 1
    setup.state.memory["verification_diff_sha256"] = "outdated"
    assert "Workspace changed" in finish(setup.check, setup.state).summary
