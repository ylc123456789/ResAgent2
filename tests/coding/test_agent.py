import subprocess
from pathlib import Path

from resagent2_contracts import (
    Capability,
    CodeModifyInput,
    CodeUnderstandInput,
    ModuleStatus,
    ModuleTaskRequest,
    TaskBudget,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSource,
)
from resagent2_coding import NativeCodingAgent
from resagent2_runtime import ScriptedLLMClient


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


def request(
    root: Path,
    *,
    capability: Capability,
    verification_commands: list[str] | None = None,
) -> ModuleTaskRequest:
    if capability == Capability.CODE_MODIFY:
        inputs = CodeModifyInput(
            instructions="Add a docstring to add and keep behavior unchanged",
            allowed_paths=["util.py", "new_helper.py"],
            verification_commands=verification_commands or [],
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
            source=WorkspaceSource.EXISTING,
        ),
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


def test_modify_profile_passes_legacy_docstring_golden_case(tmp_path) -> None:
    init_repo(tmp_path)
    verify = 'python -c "import util; assert util.add(2, 3) == 5"'
    agent = NativeCodingAgent(
        ScriptedLLMClient(
            [
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
                {"tool": "run_verification", "arguments": {}},
                {
                    "tool": "finish",
                    "arguments": {
                        "result": {"summary": "Added and verified the docstring"}
                    },
                },
            ]
        )
    )

    result = agent.invoke(
        request(
            tmp_path,
            capability=Capability.CODE_MODIFY,
            verification_commands=[verify],
        )
    )

    assert result.status == ModuleStatus.COMPLETED, result.model_dump(mode="json")
    assert result.payload["changed_files"] == ["util.py"]
    assert result.payload["verification_passed"] is True
    assert {artifact.kind for artifact in result.artifacts} == {
        "code_patch",
        "code_change",
    }
    assert "Return the sum" in (tmp_path / "util.py").read_text(encoding="utf-8")


def test_new_file_becomes_a_code_artifact(tmp_path) -> None:
    init_repo(tmp_path)
    agent = NativeCodingAgent(
        ScriptedLLMClient(
            [
                {
                    "tool": "create_file",
                    "arguments": {"path": "new_helper.py", "content": "VALUE = 1\n"},
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
                        "new_text": "return a - b",
                    },
                },
                {"tool": "run_verification", "arguments": {}},
                {
                    "tool": "finish",
                    "arguments": {"result": {"summary": "Incorrectly done"}},
                },
            ]
        )
    )

    result = agent.invoke(
        request(
            tmp_path,
            capability=Capability.CODE_MODIFY,
            verification_commands=[
                'python -c "import util; assert util.add(2, 3) == 5"'
            ],
        )
    )

    assert result.status == ModuleStatus.FAILED
    assert result.error is not None and result.error.retryable is False
    assert len(result.artifacts) == 1
    assert result.artifacts[0].metadata["diagnostic"] is True


def test_verification_command_cannot_silently_change_code(tmp_path) -> None:
    init_repo(tmp_path)
    agent = NativeCodingAgent(
        ScriptedLLMClient(
            [
                {
                    "tool": "create_file",
                    "arguments": {"path": "new_helper.py", "content": "VALUE = 1\n"},
                },
                {"tool": "run_verification", "arguments": {}},
                {
                    "tool": "finish",
                    "arguments": {"result": {"summary": "Done"}},
                },
            ]
        )
    )
    mutating_command = (
        'python -c "from pathlib import Path; '
        "Path('new_helper.py').write_text('VALUE = 2\\n')\""
    )

    result = agent.invoke(
        request(
            tmp_path,
            capability=Capability.CODE_MODIFY,
            verification_commands=[mutating_command],
        )
    )

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
