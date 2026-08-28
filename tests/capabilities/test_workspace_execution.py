import hashlib
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from resagent2_contracts import (
    AgentOwner,
    ArtifactRef,
    WorkspaceGrant,
    WorkspaceMode,
    WorkspaceSourceKind,
)
from resagent2_capabilities import (
    ArtifactReadError,
    GitWorkspace,
    GitWorkspaceError,
    ProcessRunner,
    ReadFileTool,
    RegisteredArtifactReader,
    ReplaceTextTool,
    UnsafeCommandError,
    VerificationCommandPolicy,
    WorkspaceBoundary,
    WorkspacePermissionError,
    parse_command,
)
from resagent2_runtime import AgentState


def grant(root: Path, *, mode: WorkspaceMode = WorkspaceMode.READ_WRITE) -> WorkspaceGrant:
    return WorkspaceGrant(
        root=str(root),
        mode=mode,
        allowed_paths=["."],
        denied_paths=["denied"],
        source=WorkspaceSourceKind.LOCAL,
    )


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    (root / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)


def test_workspace_rejects_traversal_reserved_paths_and_escaping_symlink(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)
    boundary = WorkspaceBoundary(grant(root))

    with pytest.raises(WorkspacePermissionError):
        boundary.resolve_read_file("../outside.txt")
    with pytest.raises(WorkspacePermissionError):
        boundary.resolve_read_file(".git/config")
    with pytest.raises(WorkspacePermissionError):
        boundary.resolve_read_file("link.txt")


def test_read_only_workspace_cannot_resolve_a_write(tmp_path) -> None:
    boundary = WorkspaceBoundary(grant(tmp_path, mode=WorkspaceMode.READ_ONLY))

    with pytest.raises(WorkspacePermissionError, match="read-only"):
        boundary.resolve_write_file("new.py", must_be_new=True)


@pytest.mark.parametrize(
    "command",
    [
        "python test.py && echo done",
        "python test.py | tee log",
        "python test.py > output.txt",
        "python $(which pytest)",
        "python test.py\nrm file",
    ],
)
def test_command_parser_rejects_shell_composition(command: str) -> None:
    with pytest.raises(UnsafeCommandError):
        parse_command(command)


def test_verification_policy_denies_destructive_and_non_verification_commands() -> None:
    policy = VerificationCommandPolicy()
    denied = [
        "rm -rf .",
        "git clean -fd",
        "python -m pip install xxx",
        "curl https://example.com",
        "bash script.sh",
        "git commit -m x",
    ]
    for command in denied:
        assert policy.check([command]).allowed is False, command


def test_verification_policy_allows_recognised_test_runners() -> None:
    policy = VerificationCommandPolicy()
    allowed = [
        "python -m py_compile train.py",
        "python -m pytest tests/",
        "python -m unittest",
        "pytest -q",
        "cargo test",
        "cargo check",
        "go test ./...",
        "npm test",
        "pnpm test",
        "yarn test",
    ]
    for command in allowed:
        assert policy.check([command]).allowed is True, command


def test_process_runner_uses_argv_and_writes_logs(tmp_path) -> None:
    boundary = WorkspaceBoundary(grant(tmp_path))
    result = ProcessRunner(boundary).run(
        f'{sys.executable} -c "print(42)"',
        log_dir=".resagent2/test",
        index=1,
        timeout_seconds=10,
    )

    assert result.exit_code == 0
    assert (tmp_path / result.stdout_path).read_text(encoding="utf-8").strip() == "42"


def test_process_runner_marks_timeout_and_stops_process(tmp_path) -> None:
    boundary = WorkspaceBoundary(grant(tmp_path))
    result = ProcessRunner(boundary).run(
        f'{sys.executable} -c "import time; time.sleep(5)"',
        log_dir=".resagent2/timeout",
        index=1,
        timeout_seconds=1,
    )

    assert result.timed_out is True
    assert result.exit_code != 0


def test_git_workspace_requires_clean_baseline_and_includes_new_files(tmp_path) -> None:
    init_repo(tmp_path)
    repository = GitWorkspace(WorkspaceBoundary(grant(tmp_path)))
    repository.require_clean()
    (tmp_path / "new.py").write_text("value = 1\n", encoding="utf-8")

    assert repository.changed_paths() == ["new.py"]
    assert "new.py" in repository.diff()


def test_git_baseline_isolates_attempt_increment(tmp_path) -> None:
    init_repo(tmp_path)
    repository = GitWorkspace(WorkspaceBoundary(grant(tmp_path)))

    # Task A leaves tracked + untracked changes in the shared workspace.
    (tmp_path / "tracked.txt").write_text("changed-a\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    baseline = repository.snapshot()

    # Task B's increment is only tracked.txt (again) and the new b.py.
    (tmp_path / "tracked.txt").write_text("changed-b\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b = 1\n", encoding="utf-8")

    changed = repository.changed_paths_since(baseline)
    assert changed == ["b.py", "tracked.txt"]
    assert "a.py" not in changed

    diff = repository.diff_since(baseline)
    assert "changed-b" in diff
    assert "a.py" not in diff


def test_require_clean_rejects_dirty_workspace(tmp_path) -> None:
    init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    repository = GitWorkspace(WorkspaceBoundary(grant(tmp_path)))
    with pytest.raises(GitWorkspaceError, match="clean"):
        repository.require_clean()


def test_symlink_cannot_bypass_denied_paths(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    init_repo(root)
    (root / "denied").mkdir()
    secret = root / "denied" / "secret.txt"
    secret.write_text("secret\n", encoding="utf-8")
    (root / "alias.txt").symlink_to(secret)
    boundary = WorkspaceBoundary(grant(root))

    with pytest.raises(WorkspacePermissionError):
        boundary.resolve_read_file("alias.txt")
    with pytest.raises(WorkspacePermissionError):
        boundary.resolve_write_file("alias.txt")


def test_registered_artifact_reader_verifies_hash(tmp_path) -> None:
    frozen = tmp_path / "artifact.txt"
    frozen.write_text("evidence", encoding="utf-8")
    artifact = ArtifactRef(
        id="artifact_input",
        kind="text",
        producer=AgentOwner.SCIENTIFIC,
        run_id="run_artifact",
        task_id="task_artifact",
        attempt_number=1,
        uri=frozen.as_uri(),
        sha256=hashlib.sha256(frozen.read_bytes()).hexdigest(),
        media_type="text/plain",
        summary="input",
    )
    reader = RegisteredArtifactReader([artifact])

    assert reader.read_text("artifact_input")["content"] == "evidence"
    frozen.write_text("tampered", encoding="utf-8")
    with pytest.raises(ArtifactReadError, match="sha256"):
        reader.read_text("artifact_input")


def _agent_state() -> AgentState:
    now = datetime.now(UTC)
    return AgentState(
        session_id="session_test",
        agent_name="reader",
        owner=AgentOwner.CODING,
        run_id="run_test",
        task_id="task_test",
        attempt_number=1,
        created_at=now,
        updated_at=now,
    )


def test_git_diff_respects_denied_paths(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    init_repo(root)
    (root / "denied").mkdir()
    (root / "denied" / "secret.txt").write_text("secret content\n", encoding="utf-8")
    repository = GitWorkspace(WorkspaceBoundary(grant(root)))
    (root / "tracked.txt").write_text("changed\n", encoding="utf-8")

    assert "tracked.txt" in repository.changed_paths()
    assert "denied/secret.txt" not in repository.changed_paths()
    assert "secret content" not in repository.diff()


def test_resolve_system_write_ignores_allowed_paths(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    boundary = WorkspaceBoundary(
        WorkspaceGrant(
            root=str(root),
            mode=WorkspaceMode.READ_WRITE,
            allowed_paths=["src"],
            source=WorkspaceSourceKind.LOCAL,
        )
    )
    resolved = boundary.resolve_system_write(".resagent2/runs/run_x/task_x/a_1/x.patch")
    assert resolved.is_relative_to(root)


def test_read_file_rejects_oversized_files(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "big.txt").write_text("x" * 2_000_000, encoding="utf-8")
    boundary = WorkspaceBoundary(grant(root))
    tool = ReadFileTool(boundary, max_bytes=1_000_000)
    with pytest.raises(ValueError, match="too large"):
        tool.execute(_agent_state(), tool.input_model(path="big.txt"))


def test_replace_text_preserves_leading_whitespace_in_old_text(tmp_path) -> None:
    # old_text is an exact needle: indentation must survive model validation.
    root = tmp_path / "repo"
    root.mkdir()
    (root / "m.py").write_text("class A:\n    def f(self):\n        return 1\n", encoding="utf-8")
    boundary = WorkspaceBoundary(grant(root))
    tool = ReplaceTextTool(boundary)

    args = tool.input_model(
        path="m.py",
        old_text="    def f(self):\n        return 1",
        new_text="    def f(self):\n        return 2",
    )
    assert args.old_text == "    def f(self):\n        return 1"

    tool.execute(_agent_state(), args)
    assert (root / "m.py").read_text(encoding="utf-8") == (
        "class A:\n    def f(self):\n        return 2\n"
    )


def test_replace_text_does_not_grow_indentation(tmp_path) -> None:
    # The historical bug stripped old_text's indentation, so the replacement
    # left the original spaces AND prepended new ones (4 -> 8). This guards it.
    root = tmp_path / "repo"
    root.mkdir()
    (root / "m.py").write_text("class A:\n    def f(self):\n        return 1\n", encoding="utf-8")
    tool = ReplaceTextTool(WorkspaceBoundary(grant(root)))

    tool.execute(
        _agent_state(),
        tool.input_model(
            path="m.py",
            old_text="    def f(self):\n        return 1",
            new_text="    def f(self):\n        return 2",
        ),
    )
    assert (root / "m.py").read_text(encoding="utf-8") == (
        "class A:\n    def f(self):\n        return 2\n"
    )


def test_replace_text_rejects_ambiguous_match_without_touching_file(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    original = "def f(self):\n    pass\ndef g(self):\n    pass\n"
    (root / "m.py").write_text(original, encoding="utf-8")
    tool = ReplaceTextTool(WorkspaceBoundary(grant(root)))

    with pytest.raises(ValueError, match="found 2"):
        tool.execute(
            _agent_state(),
            tool.input_model(
                path="m.py",
                old_text="    pass",
                new_text="    return 1",
            ),
        )
    # The file must be untouched after a rejected ambiguous replacement.
    assert (root / "m.py").read_text(encoding="utf-8") == original


def test_replace_text_rejects_empty_old_text(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "m.py").write_text("x = 1\n", encoding="utf-8")
    tool = ReplaceTextTool(WorkspaceBoundary(grant(root)))

    with pytest.raises(Exception, match="old_text must not be empty"):
        tool.input_model(path="m.py", old_text="", new_text="y")
