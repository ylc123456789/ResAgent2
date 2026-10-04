"""One environment-generation validity rule drives context and recorded evidence."""

import json
import shlex
import sys
from datetime import UTC, datetime
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from resagent2_capabilities import (
    AuditEnvTool,
    PrepareEnvironmentTool,
    RunSetupTool,
)
from resagent2_coding.verification import RunVerificationTool
from resagent2_components import (
    EnvironmentBinding,
    EnvironmentManagerError,
    PreparedEnvironment,
    ProcessRunner,
    WorkspaceBoundary,
)
from resagent2_contracts import (
    AgentOwner, VerificationResult, WorkspaceGrant, WorkspaceAccess, WorkspaceSourceKind,
)
from resagent2_coding.completion import CodingCompletionCheck, derive_control_state
from resagent2_runtime import AgentState, FinishCandidate
from resagent2_runtime.budget import DeadlineExceededError


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
    boundary = WorkspaceBoundary(WorkspaceGrant(root=str(tmp_path), source=WorkspaceSourceKind.LOCAL, access=WorkspaceAccess(read_paths=['.'], write_paths=['.'])))
    manager = Manager(tmp_path)
    binding = EnvironmentBinding(manager, run_id="run_test", workspace_id="ws_test")
    binding.certified = True
    repository = SimpleNamespace(
        diff_since=lambda baseline: DIFF,
        changed_paths_since=lambda baseline: ["code.py"],
        deleted_paths_since=lambda baseline: [],
    )
    baseline = object()
    check = CodingCompletionCheck(
        repository, boundary,
        baseline=baseline, env_binding=binding,
    )
    now = datetime.now(UTC)
    result = Runner(boundary).run("python -m pytest")
    state = AgentState(
        session_id="session_test", agent_name="coding", owner=AgentOwner.CODING,
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
    decision = check.evaluate(state, FinishCandidate(report="done"))
    records = [json.loads(item.content) for item in decision.artifacts if item.kind == "verification_result"]
    return records[0] if records else {"covers_current_workspace": False, "issue": derive_control_state(state, check.env_binding)["verification_issue"]}


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
    assert finish(setup.check, setup.state)["covers_current_workspace"]
    generation = setup.binding.generation
    tool = RunSetupTool(
        Runner(setup.boundary, exit_code=exit_code), setup.binding,
        log_dir=str(setup.root / "setup"), timeout_seconds=30,
    )

    observation = tool.execute(setup.state, tool.input_model(command="pip install numpy"))

    assert observation.ok is (exit_code == 0)
    assert setup.binding.generation != generation
    assert setup.binding.certified is False
    assert not finish(setup.check, setup.state)["covers_current_workspace"]
    control = derive_control_state(setup.state, setup.binding)
    assert control["verification_stale"]
    assert control["verification_passed"] is True
    generation = setup.binding.generation
    assert reaudit(setup.binding, setup.state).ok
    assert setup.binding.generation == generation
    assert not finish(setup.check, setup.state)["covers_current_workspace"]
    control = derive_control_state(setup.state, setup.binding)
    assert control["verification_stale"]
    assert control["verification_passed"] is True
    assert reverify(setup).ok
    assert setup.state.memory["verification_environment_generation"] == generation
    assert finish(setup.check, setup.state)["covers_current_workspace"]
    control = derive_control_state(setup.state, setup.binding)
    assert not control["verification_stale"]
    assert control["verification_passed"] is True


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
    assert not finish(setup.check, setup.state)["covers_current_workspace"]


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
    assert not finish(setup.check, setup.state)["covers_current_workspace"]


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
    assert finish(setup.check, setup.state)["covers_current_workspace"]


def test_new_binding_and_reaudit_require_new_verification(setup):
    previous = setup.binding.generation
    restored = EnvironmentBinding(setup.manager, run_id="run_test", workspace_id="ws_test")
    assert restored.generation != previous
    assert not restored.certified
    setup.binding = restored
    setup.check.env_binding = restored
    assert reaudit(restored, setup.state).ok

    assert not finish(setup.check, setup.state)["covers_current_workspace"]
    control = derive_control_state(setup.state, restored)
    assert control["verification_stale"]
    assert control["verification_passed"] is True
    assert reverify(setup).ok
    assert finish(setup.check, setup.state)["covers_current_workspace"]


@pytest.mark.parametrize("exit_code", [1, 2])
def test_current_failed_verification_remains_current_and_reports_failure(setup, exit_code):
    observation = reverify(setup, exit_code=exit_code)

    assert observation.ok is False
    assert setup.state.memory["verification_revision"] == setup.state.memory["edit_revision"]
    control = derive_control_state(setup.state, setup.binding)
    assert not control["verification_stale"]
    assert control["verification_passed"] is False
    assert control["verification_issue"] is None
    decision = setup.check.evaluate(
        setup.state,
        FinishCandidate(report="Explained the failure of the requested check"),
    )
    assert decision.complete
    assert decision.report == "Explained the failure of the requested check"
    record = next(json.loads(item.content) for item in decision.artifacts
                  if item.kind == "verification_result")
    assert record["covers_current_workspace"]
    assert record["passed"] is False
    assert record["issue"] is None
    assert record["results"][0]["exit_code"] == exit_code


@pytest.mark.parametrize("results", [[], None, ["invalid"], [{"exit_code": 0}]])
def test_missing_or_invalid_results_cannot_satisfy_verification(setup, results):
    setup.state.memory["verification_results"] = results

    control = derive_control_state(setup.state, setup.binding)
    assert control["verification_passed"] is None
    assert not control["verification_stale"]
    assert control["verification_issue"]
    decision = finish(setup.check, setup.state)
    assert not decision["covers_current_workspace"]
    assert decision["issue"] == derive_control_state(setup.state, setup.binding)["verification_issue"]


def test_latest_edit_and_workspace_digest_still_enforced(setup):
    setup.state.memory["edit_revision"] = 2
    record = finish(setup.check, setup.state)
    assert not record["covers_current_workspace"]
    assert record["passed"] is True
    control = derive_control_state(setup.state, setup.binding)
    assert control["verification_stale"]
    assert control["verification_passed"] is True
    setup.state.memory["edit_revision"] = 1
    setup.state.memory["verification_diff_sha256"] = "outdated"
    assert "Workspace changed" in finish(setup.check, setup.state)["issue"]


@pytest.mark.parametrize("restore_session", [False, True])
def test_reverification_preserves_logs_at_same_revision_and_timestamp(setup, monkeypatch, restore_session):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 23, 8, 0, tzinfo=UTC)

    monkeypatch.setattr("resagent2_coding.verification.datetime", FixedDatetime)
    source = setup.root / "test_probe.py"
    source.write_text(
        "import os, sys, unittest\n"
        "class Probe(unittest.TestCase):\n"
        "    def test_result(self):\n"
        "        print(os.environ['VERIFY_LABEL'])\n"
        "        print(os.environ['VERIFY_LABEL'], file=sys.stderr)\n"
        "        self.assertEqual(os.environ['VERIFY_OK'], '1')\n",
        encoding="utf-8",
    )
    original_source = source.read_bytes()

    def verification(label, ok):
        return RunVerificationTool(
            ProcessRunner(setup.boundary), setup.repository,
            log_root=str(setup.root / "verify"), timeout_seconds=30,
            baseline=setup.baseline,
            extra_env={"VERIFY_LABEL": label, "VERIFY_OK": str(ok)},
        )

    tool = verification("first-failed", 0)
    arguments = tool.input_model(commands=[f"{shlex.quote(sys.executable)} -m unittest test_probe"])
    first = tool.execute(setup.state, arguments)
    assert not first.ok
    setup.state.memory.update(first.memory_updates)
    previous = first.value["results"][0]
    old_logs = {key: Path(previous[key]).read_bytes() for key in ("stdout_path", "stderr_path")}
    assert all(b"first-failed" in content for content in old_logs.values())

    if restore_session:
        setup.state = AgentState.model_validate_json(setup.state.model_dump_json())
        tool = verification("second-passed", 1)
    else:
        tool.extra_env.update(VERIFY_LABEL="second-passed", VERIFY_OK="1")
    second = tool.execute(setup.state, arguments)

    assert second.ok
    assert setup.state.memory["edit_revision"] == 1
    assert source.read_bytes() == original_source
    current = second.value["results"][0]
    for key, content in old_logs.items():
        assert previous[key] != current[key]
        assert Path(previous[key]).read_bytes() == content
        assert b"second-passed" in Path(current[key]).read_bytes()



@pytest.mark.parametrize("command", [
    "/usr/bin/python3 -m unittest", "./python -m pytest", "/tmp/pytest -q",
])
def test_bound_verification_rejects_external_python_before_approval(setup, command):
    from resagent2_capabilities.permissions import OperationPermissionPolicy
    from resagent2_contracts import AgentPermissions, AgentRequest, TaskBudget
    from resagent2_runtime import AgentAction

    class UnusedRunner(Runner):
        def run(self, *args, **kwargs):
            raise AssertionError("Rejected interpreter must not be executed")

    tool = RunVerificationTool(
        UnusedRunner(setup.boundary), setup.repository,
        log_root=str(setup.root / "verify"), timeout_seconds=30,
        baseline=setup.baseline, env_binding=setup.binding,
    )
    request = AgentRequest(
        run_id="run_test", task_id="task_test", attempt_number=1,
        agent="coding", instruction="Verify using the bound environment",
        permissions=AgentPermissions(execute_commands=True),
        confirm_commands=True, workspace=setup.boundary.grant,
        budget=TaskBudget(max_llm_calls=4, timeout_seconds=30),
    )
    policy = OperationPermissionPolicy(
        [tool], boundary=setup.boundary, binding=setup.binding, request=request,
    )
    decision = policy.check(
        AgentAction(tool="run_verification", arguments={"commands": [command]}),
        setup.state, request,
    )
    assert decision.outcome == "deny"
    assert "bound interpreter" in decision.reason
    with pytest.raises(ValueError, match="bound interpreter"):
        tool.execute(setup.state, tool.input_model(commands=[command]))
    assert setup.binding.certified


@pytest.mark.parametrize("command", [
    "python -m unittest test_runtime", "python3 -m unittest test_runtime",
    "pytest -q -s test_runtime.py",
])
def test_python_verification_uses_bound_interpreter_despite_path_shadowing(
    setup, monkeypatch, command,
):
    import os

    setup.binding.current = PreparedEnvironment(
        env_id="resenv_test", prefix=Path(sys.prefix), python_version="3.12",
    )
    # Exercise the real ProcessRunner without depending on a host conda CLI.
    monkeypatch.setattr(setup.binding, "argv_prefix", lambda: [])
    (setup.root / "code.py").unlink()  # Avoid shadowing stdlib code imported by pytest.
    fake_bin = setup.root / "fake-bin"
    fake_bin.mkdir()
    for name in ("python", "python3", "pytest"):
        executable = fake_bin / name
        executable.write_text("#!/bin/sh\necho WRONG_INTERPRETER\nexit 99\n")
        executable.chmod(0o755)
    (setup.root / "test_runtime.py").write_text(
        "import os,sys,unittest\n"
        "class BoundRuntime(unittest.TestCase):\n"
        " def test_prefix(self):\n"
        "  print('BOUND_PREFIX='+sys.prefix,flush=True)\n"
        "  self.assertEqual(sys.prefix,os.environ['EXPECTED_PREFIX'])\n",
    )
    tool = RunVerificationTool(
        ProcessRunner(setup.boundary), setup.repository,
        log_root=str(setup.root / "verify"), timeout_seconds=30,
        baseline=setup.baseline, env_binding=setup.binding,
        extra_env={"EXPECTED_PREFIX": sys.prefix, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]},
    )
    observation = tool.execute(setup.state, tool.input_model(commands=[command]))
    result = observation.value["results"][0]
    assert observation.ok
    assert shlex.split(result["command"])[0] == str(Path(sys.prefix) / "bin" / "python")
    stdout = Path(result["stdout_path"]).read_text()
    assert "BOUND_PREFIX=" + sys.prefix in stdout
    assert "WRONG_INTERPRETER" not in stdout
    if command.startswith("pytest"):
        assert shlex.split(result["command"])[1:3] == ["-m", "pytest"]


@pytest.mark.parametrize("error", [
    RuntimeError("Git diagnostic unavailable"),
    DeadlineExceededError(
        "execution deadline exceeded",
    ),
])
def test_post_command_diagnostic_failure_preserves_receipts_and_stays_stale(
    setup, error,
):
    calls = []

    def diff_since(baseline):
        calls.append(baseline)
        if len(calls) > 1:
            raise error
        return DIFF

    setup.repository.diff_since = diff_since
    setup.repository.changed_paths_since = lambda _: []
    (setup.root / "stdout.log").write_text("Executed the real check")
    observation = reverify(setup)
    assert not observation.ok
    assert observation.value["workspace_unchanged"] is False
    assert observation.value["workspace_error"].endswith(str(error))
    assert len(observation.value["results"]) == 1
    assert observation.value["results"][0]["exit_code"] == 0
    assert observation.value["results"][0]["stdout_tail"] == "Executed the real check"
    assert setup.state.memory["verification_diff_sha256"] is None
    control = derive_control_state(setup.state, setup.binding)
    assert control["verification_stale"]
    assert control["verification_passed"] is True  # Execution result, independent of freshness.
    record = finish(setup.check, setup.state)
    assert not record["covers_current_workspace"]
    assert record["passed"] is True
    assert "freshness could not be confirmed" in record["issue"]
    assert len(calls) == 2  # Completion does not retry the failed freshness probe.
    setup.repository.diff_since = lambda _: DIFF
    assert reverify(setup).ok
    assert finish(setup.check, setup.state)["covers_current_workspace"]


@pytest.mark.parametrize("failure", ["shared_deadline", "batch_deadline", "runner_io"])
def test_partial_verification_keeps_actual_results_without_inventing_missing_outcomes(
    setup, monkeypatch, failure,
):
    from resagent2_runtime.budget import DeadlineExceededError

    commands = ["python -m unittest first", "python -m unittest second", "python -m unittest third"]

    class PartialRunner(Runner):
        calls = []

        def run(self, command, **kwargs):
            self.calls.append(command)
            if len(self.calls) == 2:
                if failure == "runner_io":
                    raise OSError("Could not start the next interpreter")
                raise DeadlineExceededError("execution deadline exceeded")
            return super().run(command, **kwargs)

    runner = PartialRunner(setup.boundary)
    if failure == "batch_deadline":
        times = iter([0, 0, 31])
        monkeypatch.setattr("resagent2_coding.verification.monotonic", lambda: next(times))
    tool = RunVerificationTool(
        runner, setup.repository, log_root=str(setup.root / "verify"),
        timeout_seconds=30, baseline=setup.baseline, env_binding=setup.binding,
    )
    observation = tool.execute(setup.state, tool.input_model(commands=commands))
    setup.state.memory.update(observation.memory_updates)
    assert not observation.ok
    assert len(observation.value["results"]) == 1
    assert observation.value["results"][0]["exit_code"] == 0
    assert observation.value["unrecorded_commands"] == commands[1:]
    assert len(runner.calls) == (1 if failure == "batch_deadline" else 2)
    control = derive_control_state(setup.state, setup.binding)
    assert control["verification_stale"]
    assert control["verification_passed"] is False
    record = finish(setup.check, setup.state)
    assert not record["covers_current_workspace"]
    assert record["passed"] is False
    assert record["unrecorded_commands"] == commands[1:]
    assert len(record["results"]) == 1
    assert "no recorded outcome" in record["issue"]
    if failure == "batch_deadline":
        monkeypatch.setattr("resagent2_coding.verification.monotonic", __import__("time").monotonic)
    assert reverify(setup).ok
    assert setup.state.memory["verification_unrecorded_commands"] == []


def test_loop_persists_real_verification_receipt_when_shared_deadline_expires(
    tmp_path,
):
    import subprocess
    from resagent2_components import GitWorkspace
    from resagent2_contracts import AgentPermissions, AgentRequest, ErrorCode, TaskBudget
    from resagent2_runtime import (
        AgentDefinition, AgentLoop, AllowListPermissionPolicy,
        InMemorySessionStore, NativeToolCall, ToolCallTurn,
    )
    from resagent2_runtime.budget import execution_budget

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "test_quick.py").write_text(
        "import unittest\nclass QuickCheck(unittest.TestCase):\n"
        " def test_execution(self):\n"
        "  print('execution_recorded',flush=True)\n"
        "  self.assertEqual(1+1,2)\n",
    )
    boundary = WorkspaceBoundary(WorkspaceGrant(
        root=str(tmp_path), source="local",
        access=WorkspaceAccess(read_paths=["."], write_paths=["."]),
    ))
    repository = GitWorkspace(boundary)
    baseline = repository.snapshot()
    clock = [0.0]

    class DeadlineAfterCommand(ProcessRunner):
        def run(self, *args, **kwargs):
            result = super().run(*args, **kwargs)
            clock[0] = 31.0
            return result

    tool = RunVerificationTool(
        DeadlineAfterCommand(boundary), repository, log_root=str(tmp_path / ".resagent2" / "logs"),
        timeout_seconds=30, baseline=baseline,
    )
    store = InMemorySessionStore()
    request = AgentRequest(
        run_id="run_deadline", task_id="task_deadline", attempt_number=1,
        agent="coding", instruction="Run the small correctness check",
        workspace=boundary.grant, permissions=AgentPermissions(execute_commands=True),
        budget=TaskBudget(max_llm_calls=2, timeout_seconds=30),
    )
    class NativeClient:
        tool_session_key = "verification-deadline-test"

        def next_tool_call(self, context, schemas, turns, **kwargs):
            return ToolCallTurn(tool_calls=[NativeToolCall(
                id="verify", name="run_verification", arguments=json.dumps({
                    "commands": [shlex.quote(sys.executable) + " -m unittest test_quick"],
                }),
            )])

    definition = AgentDefinition(
        name="coding", owner=AgentOwner.CODING, system_prompt="Verify the supplied code.",
        tools=(tool,), llm_client=NativeClient(),
        context_builder=lambda *_: [],
        permission_policy=AllowListPermissionPolicy({tool.name}),
        completion_check=CodingCompletionCheck(repository, boundary, baseline=baseline),
    )
    with execution_budget(max_llm_calls=2, timeout_seconds=30, clock=lambda: clock[0]):
        result = AgentLoop(store=store).run(definition, request, session_id="session_deadline")
    assert result.status == "failed"
    assert result.error.code == ErrorCode.TIMEOUT
    state = store.load("session_deadline")
    records = state.memory["verification_results"]
    assert len(records) == 1
    assert records[0]["exit_code"] == 0
    assert records[0]["timed_out"] is False
    assert "execution_recorded" in Path(records[0]["stdout_path"]).read_text()
    assert "DeadlineExceededError" in state.memory["verification_workspace_error"]
    assert state.memory["verification_workspace_unchanged"] is False
    event = next(e for e in state.events if e.type == "observation" and e.tool == tool.name)
    assert event.data["value"]["results"][0]["exit_code"] == 0
    assert len(state.tool_turns[0].tool_results) == 1
