import subprocess
import sys

from datetime import UTC, datetime

from resagent2_coding import NativeCodingAgent
from resagent2_contracts import (
    AgentOwner,
    Capability,
    CodeModifyInput,
    ModuleStatus,
    ModuleTaskRequest,
    ResearchRequest,
    RunBudget,
    RunStatus,
    TaskBudget,
    TaskProposal,
    TaskStatus,
    WorkflowProposal,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSourceKind,
    WorkspaceSpec,
)
from resagent2_orchestrator import (
    InMemoryRunStore,
    ModuleBinding,
    ResearchRun,
    WorkflowScheduler,
)
from resagent2_runtime import ScriptedLLMClient


def _fake_conda(tmp_path) -> str:
    fake = tmp_path / "conda"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "if 'create' in args and '-p' in args:\n"
        "    prefix = args[args.index('-p') + 1]\n"
        "    os.makedirs(os.path.join(prefix, 'bin'), exist_ok=True)\n"
        "    open(os.path.join(prefix, 'bin', 'python'), 'a').close()\n"
        "    open(os.path.join(prefix, 'bin', 'pip'), 'a').close()\n"
        "    print('created', flush=True)\n"
        "elif 'run' in args and '-p' in args:\n"
        "    prefix = args[args.index('-p') + 1]\n"
        "    if '-c' in args:\n"
        "        print(json.dumps({'sys_executable': os.path.join(prefix, 'bin', 'python'), 'sys_prefix': prefix, 'python_version': '3.12.4', 'pip_available': True}), flush=True)\n"
        "    else:\n"
        "        print('ok', flush=True)\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return str(fake)


def _setup_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESAGENT2_CONDA_EXE", _fake_conda(tmp_path))
    monkeypatch.setenv("RESAGENT2_ENV_ROOT", str(tmp_path / "envs"))


_PREPARE = {"tool": "prepare_environment", "arguments": {"python_version": "3.12"}}
_AUDIT = {"tool": "audit_env", "arguments": {}}


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


def test_scheduler_registers_native_coding_artifacts(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "util.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "util.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
    _setup_env(tmp_path, monkeypatch)

    coding = NativeCodingAgent(
        ScriptedLLMClient(
            [
                _PREPARE, _AUDIT,
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
                    "arguments": {"commands": ["python -m py_compile util.py"]},
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
        data_root=tmp_path / "data",
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
                inputs=CodeModifyInput(
                    instructions="Change VALUE from 1 to 2",
                ),
            )
        ],
    )

    _create_run(scheduler, "run_native_e2e", request, proposal)
    run = scheduler.run_until_stable("run_native_e2e")

    assert run.workflow.tasks[0].status == TaskStatus.COMPLETED
    assert {artifact.kind for artifact in run.artifacts.values()} == {
        "code_patch",
        "code_change",
    }
    assert all(len(artifact.sha256) == 64 for artifact in run.artifacts.values())


def test_coding_resume_preserves_attempt_baseline(tmp_path, monkeypatch) -> None:
    """An edit made before a pause must stay attributed to the same Attempt.

    On resume the Git baseline is restored from persisted Session memory, never
    re-snapshotted, so the pre-pause edit is not mistaken for the Attempt's
    starting state (ADR-0011 §2).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "util.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "util.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
    _setup_env(tmp_path, monkeypatch)

    coding = NativeCodingAgent(
        ScriptedLLMClient(
            [
                _PREPARE,
                _AUDIT,
                {
                    "tool": "replace_text",
                    "arguments": {
                        "path": "util.py",
                        "old_text": "VALUE = 1",
                        "new_text": "VALUE = 2",
                    },
                },
                {
                    "tool": "ask_user",
                    "arguments": {
                        "text": "Confirm the edit?",
                        "requested_fields": ["approve"],
                        "reason": "confirm_before_edit",
                    },
                },
                _AUDIT,
                {
                    "tool": "run_verification",
                    "arguments": {"commands": ["python -m py_compile util.py"]},
                },
                {
                    "tool": "finish",
                    "arguments": {"result": {"summary": "Updated VALUE"}},
                },
            ]
        )
    )

    request = ModuleTaskRequest(
        run_id="run_resume",
        task_id="task_code",
        attempt_number=1,
        capability=Capability.CODE_MODIFY,
        goal="Change VALUE from 1 to 2",
        inputs=CodeModifyInput(instructions="Change VALUE from 1 to 2"),
        budget=TaskBudget(max_steps=20, max_llm_calls=20, timeout_seconds=60),
        workspace=WorkspaceGrant(
            root=str(repo),
            mode=WorkspaceMode.READ_WRITE,
            allowed_paths=["."],
            source=WorkspaceSourceKind.LOCAL,
        ),
        workspace_id="ws_main",
        workspace_spec=WorkspaceSpec(
            workspace_id="ws_main",
            source_kind=WorkspaceSourceKind.LOCAL,
            location=str(repo),
        ),
        output_dir=str(tmp_path / "out"),
    )

    first = coding.invoke(request)
    assert first.status == ModuleStatus.NEEDS_USER_INPUT

    resume_request = request.model_copy(
        update={"parent_session_id": first.session.id}
    )
    second = coding.invoke(resume_request)

    assert second.status == ModuleStatus.COMPLETED
    assert second.payload["changed_files"] == ["util.py"]
