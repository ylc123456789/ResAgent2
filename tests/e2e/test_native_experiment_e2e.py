import json
from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    Capability,
    ExperimentRunInput,
    ResearchRequest,
    RunBudget,
    RunStatus,
    SuccessCriterion,
    TaskProposal,
    VerificationMode,
    VerificationResult,
    WorkflowProposal,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSource,
)
from resagent2_orchestrator import InMemoryRunStore, ModuleBinding, WorkflowScheduler
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


class _NativeExperimentPort:
    """A ModulePort that drives the native Experiment tools and finalizer."""

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop

    def invoke(self, request):
        boundary = WorkspaceBoundary(request.workspace)
        env_prefix = Path(request.workspace.root) / "envs" / "resenv_x"
        runner = _FakeRunner(boundary, env_prefix)
        tools = (
            RunCommandTool(
                runner,
                argv_prefix=[],
                env_prefix=env_prefix,
                confirm_before_experiment=False,
                confirmed=True,
                timeout_seconds=request.budget.timeout_seconds,
            ),
            AuditEnvTool(
                runner,
                boundary,
                argv_prefix=[],
                env_prefix=env_prefix,
                timeout_seconds=min(request.budget.timeout_seconds, 180),
            ),
            FinishTool(),
        )
        inputs = request.inputs
        definition = AgentDefinition(
            name="experiment-run",
            owner=AgentOwner.EXPERIMENT,
            system_prompt=EXPERIMENT_PROMPT,
            tools=tools,
            llm_client=ScriptedLLMClient(
                [
                    {"tool": "audit_env", "arguments": {}},
                    {"tool": "run_command", "arguments": {"command": "python train.py"}},
                    {
                        "tool": "finish",
                        "arguments": {
                            "result": {
                                "summary": "done",
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
                expected_metrics=list(inputs.expected_metrics),
                expected_artifacts=list(inputs.expected_artifacts),
                env_id="resenv_x",
                repo_url="https://example.com/repo.git",
                commit="abc",
            ),
            action_type=ExperimentAction,
        )
        return self._loop.run(
            definition,
            request,
            session_id=f"session_{request.task_id}_{request.attempt_number}",
            initial_memory={
                "environment": {"env_id": "resenv_x", "env_prefix": str(env_prefix)},
                "hardware": "",
                "repo": {"repo_url": "https://example.com/repo.git", "commit": "abc"},
                "command_count": 0,
                "env_certified": False,
                "experiment_success_count": 0,
                "workspace_baseline": {},
            },
        )


def test_scheduler_registers_native_experiment_artifacts(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    grant = WorkspaceGrant(
        root=str(workspace),
        mode=WorkspaceMode.READ_WRITE,
        allowed_paths=["."],
        source=WorkspaceSource.EXISTING,
    )
    scheduler = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=_NativeExperimentPort(AgentLoop(store=InMemorySessionStore())),
                workspace=grant,
            )
        },
        store=InMemoryRunStore(),
        artifact_root=tmp_path / "artifacts",
    )
    request = ResearchRequest(
        goal="Run the experiment",
        budget=RunBudget(
            max_tasks=1,
            max_attempts_per_task=1,
            max_llm_calls=10,
            timeout_seconds=30,
        ),
    )
    proposal = WorkflowProposal(
        summary="native experiment E2E",
        scientific_rationale="Exercise the Phase 6 module boundary",
        tasks=[
            TaskProposal(
                id="task_experiment_native",
                capability=Capability.EXPERIMENT_RUN,
                goal="Run train.py and record accuracy",
                rationale="Test native experiment execution",
                inputs=ExperimentRunInput(
                    instructions="Run train.py and record accuracy",
                    expected_metrics=["accuracy"],
                    expected_artifacts=["metrics.json"],
                ),
                success_criteria=[
                    SuccessCriterion(
                        description="accuracy is produced",
                        verification=VerificationMode.AUTOMATIC,
                        evidence_key="metrics",
                    )
                ],
            )
        ],
    )

    scheduler.create_run("run_native_experiment", request, proposal)
    run = scheduler.run_until_stable("run_native_experiment")

    assert run.status == RunStatus.COMPLETED
    artifacts = list(run.artifacts.values())
    assert {artifact.kind for artifact in artifacts} == {"experiment_result"}
    assert all(len(artifact.sha256) == 64 for artifact in artifacts)
