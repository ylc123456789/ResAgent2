import subprocess

from resagent2_coding import NativeCodingAgent
from resagent2_contracts import (
    AgentOwner,
    Capability,
    CodeModifyInput,
    ResearchRequest,
    RunBudget,
    RunStatus,
    TaskProposal,
    WorkflowProposal,
    WorkspaceSourceKind,
    WorkspaceSpec,
)
from resagent2_orchestrator import InMemoryRunStore, ModuleBinding, WorkflowScheduler
from resagent2_runtime import ScriptedLLMClient


def test_scheduler_registers_native_coding_artifacts(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "util.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "util.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)

    coding = NativeCodingAgent(
        ScriptedLLMClient(
            [
                {
                    "tool": "replace_text",
                    "arguments": {
                        "path": "util.py",
                        "old_text": "VALUE = 1",
                        "new_text": "VALUE = 2",
                    },
                },
                {
                    "tool": "run_verification",
                    "arguments": {
                        "commands": ['python -c "import util; assert util.VALUE == 2"']
                    },
                },
                {
                    "tool": "finish",
                    "arguments": {"result": {"summary": "Updated VALUE"}},
                },
            ]
        )
    )
    scheduler = WorkflowScheduler(
        bindings={
            Capability.CODE_MODIFY: ModuleBinding(
                owner=AgentOwner.CODING,
                port=coding,
            )
        },
        store=InMemoryRunStore(),
        artifact_root=tmp_path / "artifacts",
        workspaces={
            "ws_main": WorkspaceSpec(
                workspace_id="ws_main",
                source_kind=WorkspaceSourceKind.LOCAL,
                location=str(repo),
            )
        },
    )
    request = ResearchRequest(
        goal="Update one constant",
        budget=RunBudget(
            max_tasks=1,
            max_attempts_per_task=1,
            max_llm_calls=10,
            timeout_seconds=30,
        ),
    )
    proposal = WorkflowProposal(
        work_request_id="work_legacy_initial",
        summary="Native Coding E2E",
        compilation_rationale="Exercise the Phase 5 module boundary",
        tasks=[
            TaskProposal(
                id="task_code_native",
                work_request_id="work_legacy_initial",
                capability=Capability.CODE_MODIFY,
                goal="Change VALUE from 1 to 2",
                rationale="Test native code modification",
                inputs=CodeModifyInput(
                    instructions="Change VALUE from 1 to 2",
                ),
            )
        ],
    )

    scheduler.create_run("run_native_e2e", request, proposal)
    run = scheduler.run_until_stable("run_native_e2e")

    assert run.status == RunStatus.COMPLETED
    assert {artifact.kind for artifact in run.artifacts.values()} == {
        "code_patch",
        "code_change",
    }
    assert all(len(artifact.sha256) == 64 for artifact in run.artifacts.values())
