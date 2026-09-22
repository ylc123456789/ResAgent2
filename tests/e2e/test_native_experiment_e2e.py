
from resagent2_contracts import WorkspaceAccess, RunPermissions, ExecutionLimits
from datetime import UTC, datetime
from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    WorkflowAgentKind,
    ResearchRequest,
    RunBudget,
    RunStatus,
    TaskProposal,
    TaskAcceptanceSpec,
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
            workspaces=engine._resolve_workspaces(run_id),
            status=RunStatus.RUNNING,
            created_at=now,
            updated_at=now,
        )
    )
    return engine.accept_proposal(run_id, proposal)
from resagent2_capabilities import (
    AuditEnvTool,
    PrepareEnvironmentTool,
)
from resagent2_components import (
    EnvironmentBinding,
    PreparedEnvironment,
    WorkspaceBoundary,
    WorkspaceObserver,
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
            RunCommandTool(runner, binding, timeout_seconds=request.budget.timeout_seconds),
            FinishTool(),
        )
        definition = AgentDefinition(
            name="experiment",
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
                            "report": "done",
                            "artifacts": [{
                                "kind": "experiment_result", "path": "metrics.json",
                                "media_type": "application/json", "summary": "Measured accuracy",
                            }],
                        },
                    },
                ]
            ),
            context_builder=lambda request, state, limit: build_context(request, state, binding=binding, max_context_tokens=limit),
            permission_policy=AllowListPermissionPolicy({tool.name for tool in tools}),
            completion_check=ExperimentCompletionCheck(
                WorkspaceObserver(boundary),
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
                "workspace_snapshot": {"kind": "files", "file_hashes": {}},
            },
        )


def test_scheduler_registers_native_experiment_artifacts(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    scheduler = WorkflowScheduler(bindings={WorkflowAgentKind.EXPERIMENT: ModuleBinding(owner=AgentOwner.EXPERIMENT, port=_NativeExperimentPort(AgentLoop(store=InMemorySessionStore())))}, store=InMemoryRunStore(), artifact_root=tmp_path / 'artifacts', data_root=tmp_path / 'data', workspaces={'ws_main': WorkspaceSpec(workspace_id='ws_main', source_kind=WorkspaceSourceKind.LOCAL, location=str(workspace), access=WorkspaceAccess(read_paths=['.'], write_paths=['.']))})
    request = ResearchRequest(goal='Run the experiment', budget=RunBudget(max_llm_calls=10, timeout_seconds=30), permissions=RunPermissions(execute_commands=True, prepare_environment=True), execution_limits=ExecutionLimits(max_tasks=1, max_attempts_per_task=1))
    proposal = WorkflowProposal(
        work_request_id="work_legacy_initial",
        tasks=[
            TaskProposal(
                id="task_experiment_native",
                work_request_id="work_legacy_initial",
                workflow_agent_kind=WorkflowAgentKind.EXPERIMENT,
                instruction="Run train.py and record accuracy",
                acceptance_spec=TaskAcceptanceSpec(
                    required_metric_keys=["accuracy"],
                    required_artifact_paths=["metrics.json"],
                ),
            )
        ],
    )

    _create_run(scheduler, "run_native_experiment", request, proposal)
    run = scheduler.run_until_stable("run_native_experiment")

    assert run.workflow.tasks[0].status == TaskStatus.COMPLETED
    artifacts = list(run.artifacts.values())
    assert {artifact.kind for artifact in artifacts} == {
        "experiment_result", "execution_record", "acceptance_requirements",
    }
    assert all(len(artifact.sha256) == 64 for artifact in artifacts)
