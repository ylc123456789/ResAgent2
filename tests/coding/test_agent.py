import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from resagent2_contracts import (
    AgentOwner,
    Capability,
    CodeModifyInput,
    CodeUnderstandInput,
    DatasetRef,
    ModuleStatus,
    ModuleTaskRequest,
    TaskBudget,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSourceKind,
)
from resagent2_coding import NativeCodingAgent
from resagent2_coding.context import build_context
from resagent2_runtime import AgentState, ScriptedLLMClient


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    (root / "util.py").write_text(
        "def add(a, b):\n    return a + b\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "util.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)


def _fake_conda(tmp_path: Path) -> Path:
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
    return fake


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RESAGENT2_CONDA_EXE", str(_fake_conda(tmp_path)))
    # The env root must live outside the workspace (which may be tmp_path itself),
    # otherwise the created env files leak into the Coding diff.
    monkeypatch.setenv("RESAGENT2_ENV_ROOT", str(tmp_path.parent / ("envs-" + tmp_path.name)))


_PREPARE = {"tool": "prepare_environment", "arguments": {"python_version": "3.12"}}
_AUDIT = {"tool": "audit_env", "arguments": {}}


def test_coding_context_uses_shared_dataset_catalog(tmp_path) -> None:
    task = request(tmp_path, capability=Capability.CODE_UNDERSTAND).model_copy(
        update={
            "dataset_refs": [
                DatasetRef(dataset_id="cifar10", relative_path="cifar-10")
            ]
        }
    )
    now = datetime.now(UTC)
    state = AgentState(
        session_id="session_dataset",
        agent_name="coding-understand",
        owner=AgentOwner.CODING,
        run_id=task.run_id,
        task_id=task.task_id,
        created_at=now,
        updated_at=now,
    )

    section = next(item for item in build_context(task, state) if item.name == "dataset_catalog")

    assert json.loads(section.content)["available_dataset_ids"] == ["cifar10"]


def request(root: Path, *, capability: Capability) -> ModuleTaskRequest:
    if capability == Capability.CODE_MODIFY:
        inputs = CodeModifyInput(
            instructions="Add a docstring to add and keep behavior unchanged"
        )
        mode = WorkspaceMode.READ_WRITE
    else:
        inputs = CodeUnderstandInput(question="Where is add implemented?")
        mode = WorkspaceMode.READ_ONLY
    return ModuleTaskRequest(
        run_id="run_native_coding",
        task_id="task_native_coding",
        attempt_number=1,
        capability=capability,
        goal="Exercise the native Coding Agent",
        inputs=inputs,
        budget=TaskBudget(max_steps=8, max_llm_calls=8, timeout_seconds=30),
        workspace=WorkspaceGrant(
            root=str(root),
            mode=mode,
            allowed_paths=["."],
            source=WorkspaceSourceKind.LOCAL,
        ),
        workspace_id="ws_test",
        output_dir=str(Path(root).parent / "out"),
    )


def test_read_only_profile_answers_with_observed_evidence_without_writes(tmp_path) -> None:
    init_repo(tmp_path)
    agent = NativeCodingAgent(
        ScriptedLLMClient(
            [
                {"tool": "read_file", "arguments": {"path": "util.py"}},
                {
                    "tool": "finish",
                    "arguments": {
                        "result": {
                            "answer": "add is implemented in util.py",
                            "evidence_files": ["util.py"],
                        }
                    },
                },
            ]
        )
    )

    result = agent.invoke(request(tmp_path, capability=Capability.CODE_UNDERSTAND))

    assert result.status == ModuleStatus.COMPLETED, result.model_dump(mode="json")
    assert result.payload["evidence_files"] == ["util.py"]
    assert not (tmp_path / ".resagent2").exists()
    assert subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    ).stdout == ""


def test_read_only_profile_normalizes_evidence_paths(tmp_path) -> None:
    # ``./util.py`` and ``util.py`` are the same workspace file; the evidence
    # match must not reject the claim over the spelling alias.
    init_repo(tmp_path)
    agent = NativeCodingAgent(
        ScriptedLLMClient(
            [
                {"tool": "read_file", "arguments": {"path": "./util.py"}},
                {
                    "tool": "finish",
                    "arguments": {
                        "result": {
                            "answer": "add is implemented in util.py",
                            "evidence_files": ["util.py"],
                        }
                    },
                },
            ]
        )
    )

    result = agent.invoke(request(tmp_path, capability=Capability.CODE_UNDERSTAND))

    assert result.status == ModuleStatus.COMPLETED, result.model_dump(mode="json")
    assert result.payload["evidence_files"] == ["util.py"]


def test_modify_profile_passes_legacy_docstring_golden_case(tmp_path, monkeypatch) -> None:
    init_repo(tmp_path)
    _setup_env(tmp_path, monkeypatch)
    verify = "python -m py_compile util.py"
    agent = NativeCodingAgent(
        ScriptedLLMClient(
            [
                _PREPARE, _AUDIT,
                {"tool": "read_file", "arguments": {"path": "util.py"}},
                {
                    "tool": "replace_text",
                    "arguments": {
                        "path": "util.py",
                        "old_text": "def add(a, b):\n    return a + b",
                        "new_text": (
                            "def add(a, b):\n"
                            "    \"\"\"Return the sum of two values.\"\"\"\n"
                            "    return a + b"
                        ),
                    },
                },
                {"tool": "run_verification", "arguments": {"commands": [verify]}},
                {
                    "tool": "finish",
                    "arguments": {
                        "result": {"summary": "Added and verified the docstring"}
                    },
                },
            ]
        )
    )

    result = agent.invoke(request(tmp_path, capability=Capability.CODE_MODIFY))

    assert result.status == ModuleStatus.COMPLETED, result.model_dump(mode="json")
    assert result.payload["changed_files"] == ["util.py"]
    assert result.payload["verification_passed"] is True
    assert {artifact.kind for artifact in result.artifacts} == {
        "code_patch",
        "code_change",
    }
    assert "Return the sum" in (tmp_path / "util.py").read_text(encoding="utf-8")


def test_new_file_becomes_a_code_artifact(tmp_path, monkeypatch) -> None:
    init_repo(tmp_path)
    _setup_env(tmp_path, monkeypatch)
    agent = NativeCodingAgent(
        ScriptedLLMClient(
            [
                _PREPARE, _AUDIT,
                {
                    "tool": "create_file",
                    "arguments": {"path": "new_helper.py", "content": "VALUE = 1\n"},
                },
                {
                    "tool": "run_verification",
                    "arguments": {"commands": ["python -m py_compile new_helper.py"]},
                },
                {
                    "tool": "finish",
                    "arguments": {"result": {"summary": "Created helper"}},
                },
            ]
        )
    )

    result = agent.invoke(request(tmp_path, capability=Capability.CODE_MODIFY))

    assert result.status == ModuleStatus.COMPLETED
    assert result.payload["changed_files"] == ["new_helper.py"]
    assert any(artifact.path == "new_helper.py" for artifact in result.artifacts)


def test_failed_verification_cannot_complete_and_preserves_diagnostic_patch(tmp_path) -> None:
    init_repo(tmp_path)
    agent = NativeCodingAgent(
        ScriptedLLMClient(
            [
                {
                    "tool": "replace_text",
                    "arguments": {
                        "path": "util.py",
                        "old_text": "return a + b",
                        "new_text": "return a +",
                    },
                },
                {
                    "tool": "run_verification",
                    "arguments": {"commands": ["python -m py_compile util.py"]},
                },
                {
                    "tool": "finish",
                    "arguments": {"result": {"summary": "Incorrectly done"}},
                },
            ]
        )
    )

    result = agent.invoke(request(tmp_path, capability=Capability.CODE_MODIFY))

    assert result.status == ModuleStatus.FAILED
    assert result.error is not None and result.error.retryable is False
    assert len(result.artifacts) == 1
    assert result.artifacts[0].metadata["diagnostic"] is True


def test_two_coding_tasks_share_workspace_and_isolate_artifacts(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    _setup_env(tmp_path, monkeypatch)

    # Task A modifies util.py and completes, leaving the workspace dirty.
    agent_a = NativeCodingAgent(
        ScriptedLLMClient(
            [
                _PREPARE, _AUDIT,
                {
                    "tool": "replace_text",
                    "arguments": {
                        "path": "util.py",
                        "old_text": "return a + b",
                        "new_text": "return a + b + 0",
                    },
                },
                {
                    "tool": "run_verification",
                    "arguments": {"commands": ["python -m py_compile util.py"]},
                },
                {"tool": "finish", "arguments": {"result": {"summary": "A"}}},
            ]
        )
    )
    res_a = agent_a.invoke(
        request(repo, capability=Capability.CODE_MODIFY).model_copy(
            update={"output_dir": str(tmp_path / "out_a")}
        )
    )
    assert res_a.status == ModuleStatus.COMPLETED, res_a.model_dump(mode="json")
    assert res_a.payload["changed_files"] == ["util.py"]

    # Task B runs on the same workspace: it must not be blocked by Task A's
    # uncommitted changes, and must only claim its own new file.
    agent_b = NativeCodingAgent(
        ScriptedLLMClient(
            [
                _PREPARE, _AUDIT,
                {
                    "tool": "create_file",
                    "arguments": {"path": "helper.py", "content": "VALUE = 1\n"},
                },
                {
                    "tool": "run_verification",
                    "arguments": {"commands": ["python -m py_compile helper.py"]},
                },
                {"tool": "finish", "arguments": {"result": {"summary": "B"}}},
            ]
        )
    )
    res_b = agent_b.invoke(
        request(repo, capability=Capability.CODE_MODIFY).model_copy(
            update={"output_dir": str(tmp_path / "out_b")}
        )
    )
    assert res_b.status == ModuleStatus.COMPLETED, res_b.model_dump(mode="json")
    assert res_b.payload["changed_files"] == ["helper.py"]


def test_read_only_profile_works_on_shared_dirty_workspace(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    # A previous coding task left the workspace dirty (uncommitted change).
    (repo / "util.py").write_text(
        "def add(a, b):\n    return a + b + 0\n", encoding="utf-8"
    )

    agent = NativeCodingAgent(
        ScriptedLLMClient(
            [
                {"tool": "read_file", "arguments": {"path": "util.py"}},
                {
                    "tool": "finish",
                    "arguments": {
                        "result": {
                            "answer": "add is in util.py",
                            "evidence_files": ["util.py"],
                        }
                    },
                },
            ]
        )
    )

    result = agent.invoke(request(repo, capability=Capability.CODE_UNDERSTAND))

    assert result.status == ModuleStatus.COMPLETED, result.model_dump(mode="json")


def test_disallowed_verification_command_cannot_complete(tmp_path) -> None:
    init_repo(tmp_path)
    agent = NativeCodingAgent(
        ScriptedLLMClient(
            [
                {
                    "tool": "create_file",
                    "arguments": {"path": "new_helper.py", "content": "VALUE = 1\n"},
                },
                {
                    "tool": "run_verification",
                    "arguments": {"commands": ['python -c "print(1)"']},
                },
                {
                    "tool": "finish",
                    "arguments": {"result": {"summary": "Done"}},
                },
            ]
        )
    )

    result = agent.invoke(request(tmp_path, capability=Capability.CODE_MODIFY))

    assert result.status == ModuleStatus.FAILED
    assert result.error is not None and result.error.retryable is False


def test_read_only_action_schema_rejects_write_tool(tmp_path) -> None:
    init_repo(tmp_path)
    agent = NativeCodingAgent(
        ScriptedLLMClient(
            [
                {
                    "tool": "replace_text",
                    "arguments": {
                        "path": "util.py",
                        "old_text": "a + b",
                        "new_text": "a - b",
                    },
                }
            ]
        )
    )

    result = agent.invoke(request(tmp_path, capability=Capability.CODE_UNDERSTAND))

    assert result.status == ModuleStatus.FAILED
    assert (tmp_path / "util.py").read_text(encoding="utf-8").endswith("a + b\n")


def test_audit_output_goes_to_output_dir_not_repo(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    _setup_env(tmp_path, monkeypatch)
    out = tmp_path / "out"
    agent = NativeCodingAgent(
        ScriptedLLMClient(
            [
                _PREPARE, _AUDIT,
                {
                    "tool": "replace_text",
                    "arguments": {
                        "path": "util.py",
                        "old_text": "return a + b",
                        "new_text": "return a + b + 0",
                    },
                },
                {
                    "tool": "run_verification",
                    "arguments": {"commands": ["python -m py_compile util.py"]},
                },
                {
                    "tool": "finish",
                    "arguments": {"result": {"summary": "done"}},
                },
            ]
        )
    )

    req = request(repo, capability=Capability.CODE_MODIFY).model_copy(
        update={"output_dir": str(out)}
    )
    result = agent.invoke(req)

    assert result.status == ModuleStatus.COMPLETED, result.model_dump(mode="json")
    # The patch and verification logs went to the Run output dir, not the repo.
    assert (out / "changes.patch").is_file()
    assert not (repo / ".resagent2").exists()


def test_verification_requires_audit(tmp_path, monkeypatch) -> None:
    init_repo(tmp_path)
    _setup_env(tmp_path, monkeypatch)
    agent = NativeCodingAgent(
        ScriptedLLMClient(
            [
                _PREPARE,  # no audit_env -> verification must be rejected
                {
                    "tool": "replace_text",
                    "arguments": {
                        "path": "util.py",
                        "old_text": "return a + b",
                        "new_text": "return a + b + 0",
                    },
                },
                {
                    "tool": "run_verification",
                    "arguments": {"commands": ["python -m py_compile util.py"]},
                },
                {"tool": "finish", "arguments": {"result": {"summary": "done"}}},
            ]
        )
    )

    result = agent.invoke(request(tmp_path, capability=Capability.CODE_MODIFY))

    assert result.status == ModuleStatus.FAILED


def test_code_modify_requires_workspace_id(tmp_path, monkeypatch) -> None:
    init_repo(tmp_path)
    _setup_env(tmp_path, monkeypatch)
    agent = NativeCodingAgent(ScriptedLLMClient([]))

    req = request(tmp_path, capability=Capability.CODE_MODIFY).model_copy(
        update={"workspace_id": None}
    )
    result = agent.invoke(req)

    assert result.status == ModuleStatus.BLOCKED
    DatasetRef,
