import json
from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    Capability,
    ExperimentRunInput,
    ModuleStatus,
    ModuleTaskRequest,
    TaskBudget,
    VerificationResult,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSource,
)
from resagent2_runtime import (
    AgentDefinition,
    AgentLoop,
    AllowListPermissionPolicy,
    FinishTool,
    InMemorySessionStore,
    ScriptedLLMClient,
    WorkspaceBoundary,
)

from resagent2_experiment.completion import ExperimentCompletionCheck
from resagent2_experiment.context import EXPERIMENT_PROMPT, build_context
from resagent2_experiment.models import ExperimentAction
from resagent2_experiment.tools import AuditEnvTool, RunCommandTool


class _FakeRunner:
    def __init__(self, boundary: WorkspaceBoundary, env_prefix: Path) -> None:
        self.boundary = boundary
        self.env_prefix = env_prefix

    def run(self, command, *, log_dir, index, timeout_seconds, argv_prefix=None, extra_env=None):
        stdout_rel = f"{log_dir}/command_{index:02d}.stdout"
        stderr_rel = f"{log_dir}/command_{index:02d}.stderr"
        stdout = self.boundary.resolve_system_write(stdout_rel)
        stderr = self.boundary.resolve_system_write(stderr_rel)
        stdout.parent.mkdir(parents=True, exist_ok=True)
        stderr.parent.mkdir(parents=True, exist_ok=True)
        if "audit_probe.py" in command:
            stdout.write_text(
                json.dumps({"sys_prefix": str(self.env_prefix), "python_version": "3.12"}),
                encoding="utf-8",
            )
        else:
            (self.boundary.root / "metrics.json").write_text(
                '{"accuracy": 0.9}', encoding="utf-8"
            )
            stdout.write_text("accuracy=0.9", encoding="utf-8")
        stderr.write_text("", encoding="utf-8")
        return VerificationResult(
            command=command,
            exit_code=0,
            timed_out=False,
            stdout_path=stdout_rel,
            stderr_path=stderr_rel,
            duration_seconds=0.0,
        )


def test_golden_case_flows_through_the_loop(tmp_path) -> None:
    env_prefix = tmp_path / "envs" / "resenv_x"
    env_prefix.mkdir(parents=True)
    boundary = WorkspaceBoundary(
        WorkspaceGrant(
            root=str(tmp_path),
            mode=WorkspaceMode.READ_WRITE,
            allowed_paths=["."],
            source=WorkspaceSource.EXISTING,
        )
    )
    runner = _FakeRunner(boundary, env_prefix)
    tools = (
        RunCommandTool(
            runner,
            argv_prefix=[],
            env_prefix=env_prefix,
            confirm_before_experiment=False,
            confirmed=True,
            timeout_seconds=30,
        ),
        AuditEnvTool(
            runner, boundary, argv_prefix=[], env_prefix=env_prefix, timeout_seconds=30
        ),
        FinishTool(),
    )
    definition = AgentDefinition(
        name="experiment-run",
        owner=AgentOwner.EXPERIMENT,
        system_prompt=EXPERIMENT_PROMPT,
        tools=tools,
        llm_client=ScriptedLLMClient(
            [
                {"tool": "audit_env", "arguments": {}},
                {
                    "tool": "run_command",
                    "arguments": {"command": "python train.py --epochs 2"},
                },
                {
                    "tool": "finish",
                    "arguments": {
                        "result": {
                            "summary": "trained and evaluated",
                            "metrics": {"accuracy": 0.9},
                            "evidence_files": ["metrics.json"],
                        }
                    },
                },
            ]
        ),
        context_builder=build_context,
        permission_policy=AllowListPermissionPolicy({tool.name for tool in tools}),
        completion_check=ExperimentCompletionCheck(
            boundary,
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

    result = AgentLoop(store=InMemorySessionStore()).run(
        definition,
        request,
        session_id="session_experiment",
        initial_memory={
            "environment": {"env_id": "resenv_x", "env_prefix": str(env_prefix)},
            "hardware": "",
            "repo": {"repo_url": "https://example.com/repo.git", "commit": "abc"},
            "command_count": 0,
            "env_certified": False,
        },
    )

    assert result.status == ModuleStatus.COMPLETED, result.model_dump(mode="json")
    assert result.payload["metrics"] == {"accuracy": 0.9}
    assert {artifact.kind for artifact in result.artifacts} == {"experiment_result"}
