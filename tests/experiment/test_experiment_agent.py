import json
from datetime import UTC, datetime
from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    Capability,
    DatasetRef,
    ExperimentRunInput,
    ModuleStatus,
    ModuleTaskRequest,
    TaskBudget,
    VerificationResult,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSourceKind,
)
from resagent2_capabilities import (
    AuditEnvTool,
    EnvironmentBinding,
    PreparedEnvironment,
    PrepareEnvironmentTool,
    WorkspaceBoundary,
    WorkspaceObserver,
)
from resagent2_runtime import (
    AgentDefinition,
    AgentState,
    AgentLoop,
    AllowListPermissionPolicy,
    FinishTool,
    InMemorySessionStore,
    ScriptedLLMClient,
)

from resagent2_experiment.completion import ExperimentCompletionCheck
from resagent2_experiment.context import EXPERIMENT_PROMPT, build_context
from resagent2_experiment.models import ExperimentAction
from resagent2_experiment.tools import RunCommandTool


def test_experiment_context_uses_shared_dataset_catalog() -> None:
    request = ModuleTaskRequest(
        run_id="run_test",
        task_id="task_experiment",
        attempt_number=1,
        capability=Capability.EXPERIMENT_RUN,
        goal="Run with CIFAR-10",
        inputs=ExperimentRunInput(instructions="Run with CIFAR-10"),
        dataset_refs=[DatasetRef(dataset_id="cifar10", relative_path="cifar-10")],
        budget=TaskBudget(max_steps=3, max_llm_calls=3, timeout_seconds=30),
    )
    now = datetime.now(UTC)
    state = AgentState(
        session_id="session_dataset",
        agent_name="experiment-run",
        owner=AgentOwner.EXPERIMENT,
        run_id=request.run_id,
        task_id=request.task_id,
        created_at=now,
        updated_at=now,
    )

    section = next(item for item in build_context(request, state) if item.name == "datasets")

    assert json.loads(section.content)["available_dataset_ids"] == ["cifar10"]


class _FakeManager:
    """Duck-typed manager that binds a base env without shelling to conda."""

    def __init__(self, root: Path) -> None:
        self.env_root = root
        self.conda_exe = "conda"

    def env_id(self, *, run_id: str, workspace_id: str) -> str:
        return "resenv_x"

    def inspect(self, *, run_id: str, workspace_id: str):
        return None

    def prepare(self, *, run_id: str, workspace_id: str, python_version: str):
        prefix = self.env_root / "resenv_x"
        prefix.mkdir(parents=True, exist_ok=True)
        return PreparedEnvironment(
            env_id="resenv_x", prefix=prefix, python_version=python_version
        )

    def audit(self, environment):
        return {
            "success": True,
            "sys_prefix": str(environment.prefix),
            "python_version": environment.python_version,
            "pip_available": True,
            "prefix_match": True,
            "stderr_tail": "",
        }


class _FakeRunner:
    def __init__(self, boundary: WorkspaceBoundary, *, fail: bool = False) -> None:
        self.boundary = boundary
        self.fail = fail

    def run(self, command, *, log_dir, index, timeout_seconds, argv_prefix=None, extra_env=None):
        stdout_rel = f"{log_dir}/command_{index:02d}.stdout"
        stderr_rel = f"{log_dir}/command_{index:02d}.stderr"
        stdout = self.boundary.resolve_system_write(stdout_rel)
        stderr = self.boundary.resolve_system_write(stderr_rel)
        stdout.parent.mkdir(parents=True, exist_ok=True)
        stderr.parent.mkdir(parents=True, exist_ok=True)
        exit_code = 0
        if self.fail:
            stderr.write_text("boom", encoding="utf-8")
            exit_code = 1
        else:
            (self.boundary.root / "metrics.json").write_text(
                '{"accuracy": 0.9}', encoding="utf-8"
            )
            stdout.write_text("accuracy=0.9", encoding="utf-8")
        if exit_code == 0:
            stderr.write_text("", encoding="utf-8")
        return VerificationResult(
            command=command,
            exit_code=exit_code,
            timed_out=False,
            stdout_path=stdout_rel,
            stderr_path=stderr_rel,
            duration_seconds=0.0,
        )


def _run(tmp_path: Path, actions: list, *, fail: bool = False):
    boundary = WorkspaceBoundary(
        WorkspaceGrant(
            root=str(tmp_path),
            mode=WorkspaceMode.READ_WRITE,
            allowed_paths=["."],
            source=WorkspaceSourceKind.LOCAL,
        )
    )
    manager = _FakeManager(tmp_path / "envs")
    binding = EnvironmentBinding(manager, run_id="run_test", workspace_id="ws_test")
    runner = _FakeRunner(boundary, fail=fail)
    tools = (
        PrepareEnvironmentTool(binding),
        AuditEnvTool(binding),
        RunCommandTool(
            runner,
            binding,
            confirm_before_experiment=False,
            confirmed=True,
            timeout_seconds=30,
        ),
        FinishTool(),
    )
    definition = AgentDefinition(
        name="experiment-run",
        owner=AgentOwner.EXPERIMENT,
        system_prompt=EXPERIMENT_PROMPT,
        tools=tools,
        llm_client=ScriptedLLMClient(actions),
        context_builder=build_context,
        permission_policy=AllowListPermissionPolicy({tool.name for tool in tools}),
        completion_check=ExperimentCompletionCheck(
            WorkspaceObserver(boundary),
            expected_metrics=["accuracy"],
            expected_artifacts=["metrics.json"],
            env_id="resenv_x",
            repo_url="https://example.com/repo.git",
            commit="abc",
        ),
        action_type=ExperimentAction,
    )
    request = ModuleTaskRequest(
        run_id="run_test",
        task_id="task_experiment",
        attempt_number=1,
        capability=Capability.EXPERIMENT_RUN,
        goal="Run train.py and record accuracy",
        inputs=ExperimentRunInput(
            instructions="Run train.py and record accuracy",
            expected_metrics=["accuracy"],
            expected_artifacts=["metrics.json"],
        ),
        budget=TaskBudget(max_steps=8, max_llm_calls=8, timeout_seconds=30),
    )
    return AgentLoop(store=InMemorySessionStore()).run(
        definition,
        request,
        session_id="session_experiment",
        initial_memory={
            "hardware": "",
            "repo": {"repo_url": "https://example.com/repo.git", "commit": "abc"},
            "command_count": 0,
            "experiment_success_count": 0,
            "workspace_snapshot": {"kind": "files", "file_hashes": {}},
        },
    )


_GOLDEN_ACTIONS = [
    {"tool": "prepare_environment", "arguments": {"python_version": "3.12"}},
    {"tool": "audit_env", "arguments": {}},
    {"tool": "run_command", "arguments": {"command": "python train.py --epochs 2"}},
    {
        "tool": "finish",
        "arguments": {
            "result": {
                "summary": "trained and evaluated",
                "evidence_files": ["metrics.json"],
            }
        },
    },
]


def test_golden_case_flows_through_the_loop(tmp_path) -> None:
    result = _run(tmp_path, _GOLDEN_ACTIONS)

    assert result.status == ModuleStatus.COMPLETED, result.model_dump(mode="json")
    assert result.payload["metrics"] == {"accuracy": 0.9}
    assert {artifact.kind for artifact in result.artifacts} == {"experiment_result"}


def test_failed_experiment_command_cannot_complete(tmp_path) -> None:
    result = _run(tmp_path, _GOLDEN_ACTIONS, fail=True)

    assert result.status == ModuleStatus.FAILED


def test_direct_finish_without_experiment_cannot_complete(tmp_path) -> None:
    result = _run(
        tmp_path,
        [
            {
                "tool": "finish",
                "arguments": {
                    "result": {
                        "summary": "done without running",
                        "evidence_files": ["metrics.json"],
                    }
                },
            }
        ],
    )

    assert result.status == ModuleStatus.FAILED
