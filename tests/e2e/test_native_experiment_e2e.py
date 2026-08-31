from datetime import UTC, datetime
from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    Capability,
    ExperimentRunInput,
    ResearchRequest,
    RunBudget,
    RunStatus,
    TaskProposal,
    TaskStatus,
    VerificationResult,
    WorkflowProposal,
    WorkspaceSourceKind,
    WorkspaceSpec,
)
from resagent2_orchestrator import (
    InMemoryRunStore,
    ModuleBinding,
    ResearchRun,
    WorkflowScheduler,
)


def _create_run(engine, run_id, request, proposal):
    now = datetime.now(UTC)
    engine.store.save(
        ResearchRun(
            run_id=run_id,
            request=request,
            status=RunStatus.RUNNING,
            created_at=now,
            updated_at=now,
        )
    )
    return engine.accept_proposal(run_id, proposal)
from resagent2_capabilities import (
    AuditEnvTool,
    EnvironmentBinding,
    PreparedEnvironment,
    PrepareEnvironmentTool,
    WorkspaceBoundary,
)
from resagent2_runtime import (
    AgentDefinition,
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


class _FakeManager:
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
    def __init__(self, boundary: WorkspaceBoundary) -> None:
        self.boundary = boundary

    def run(self, command, *, log_dir, index, timeout_seconds, argv_prefix=None, extra_env=None):
        stdout_rel = f"{log_dir}/command_{index:02d}.stdout"
        stderr_rel = f"{log_dir}/command_{index:02d}.stderr"
        stdout = self.boundary.resolve_system_write(stdout_rel)
        stderr = self.boundary.resolve_system_write(stderr_rel)
        stdout.parent.mkdir(parents=True, exist_ok=True)
        stderr.parent.mkdir(parents=True, exist_ok=True)
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
        manager = _FakeManager(Path(request.workspace.root) / "envs")
        binding = EnvironmentBinding(
            manager,
            run_id=request.run_id,
            workspace_id=request.workspace_id or "default",
        )
        runner = _FakeRunner(boundary)
        tools = (
            PrepareEnvironmentTool(binding),
            AuditEnvTool(binding),
            RunCommandTool(
                runner,
                binding,
                confirm_before_experiment=False,
                confirmed=True,
                timeout_seconds=request.budget.timeout_seconds,
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
                    {"tool": "prepare_environment", "arguments": {"python_version": "3.12"}},
                    {"tool": "audit_env", "arguments": {}},
                    {"tool": "run_command", "arguments": {"command": "python train.py"}},
                    {
                        "tool": "finish",
                        "arguments": {
                            "result": {
                                "summary": "done",
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
                "hardware": "",
                "repo": {"repo_url": "https://example.com/repo.git", "commit": "abc"},
                "command_count": 0,
                "experiment_success_count": 0,
                "workspace_baseline": {},
            },
        )


def test_scheduler_registers_native_experiment_artifacts(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    scheduler = WorkflowScheduler(
        bindings={
            Capability.EXPERIMENT_RUN: ModuleBinding(
                owner=AgentOwner.EXPERIMENT,
                port=_NativeExperimentPort(AgentLoop(store=InMemorySessionStore())),
            )
        },
        store=InMemoryRunStore(),
        artifact_root=tmp_path / "artifacts",
        data_root=tmp_path / "data",
        workspaces={
            "ws_main": WorkspaceSpec(
                workspace_id="ws_main",
                source_kind=WorkspaceSourceKind.LOCAL,
                location=str(workspace),
            )
        },
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
        work_request_id="work_legacy_initial",
        summary="native experiment E2E",
        compilation_rationale="Exercise the Phase 6 module boundary",
        tasks=[
            TaskProposal(
                id="task_experiment_native",
                work_request_id="work_legacy_initial",
                capability=Capability.EXPERIMENT_RUN,
                goal="Run train.py and record accuracy",
                rationale="Test native experiment execution",
                inputs=ExperimentRunInput(
                    instructions="Run train.py and record accuracy",
                    expected_metrics=["accuracy"],
                    expected_artifacts=["metrics.json"],
                ),
            )
        ],
    )

    _create_run(scheduler, "run_native_experiment", request, proposal)
    run = scheduler.run_until_stable("run_native_experiment")

    assert run.workflow.tasks[0].status == TaskStatus.COMPLETED
    artifacts = list(run.artifacts.values())
    assert {artifact.kind for artifact in artifacts} == {"experiment_result"}
    assert all(len(artifact.sha256) == 64 for artifact in artifacts)
