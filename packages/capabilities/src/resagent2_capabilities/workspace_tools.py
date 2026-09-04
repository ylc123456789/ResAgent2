"""Typed filesystem, Artifact, Git, and verification Tools."""

from __future__ import annotations

import mimetypes
import hashlib
import os
import tempfile
from pathlib import Path
from time import monotonic
from typing import cast

from pydantic import BaseModel, Field, field_validator

from resagent2_contracts import VerificationResult
from resagent2_runtime import AgentState, ToolObservation
from resagent2_runtime.models import NonEmptyStr, RuntimeModel

from .artifacts import RegisteredArtifactReader
from .environment import EnvironmentBinding
from .git import GitBaseline, GitWorkspace
from .process import ProcessRunner, VerificationCommandPolicy
from .workspace import WorkspaceBoundary


def _remember(state: AgentState, key: str, value: str) -> list[str]:
    current = state.memory.get(key, [])
    values = list(current) if isinstance(current, list) else []
    if value not in values:
        values.append(value)
    return values


class ListFilesInput(RuntimeModel):
    """Bounded workspace listing request."""

    path: str = "."
    max_files: int = Field(default=200, ge=1, le=2000)


class ListFilesTool:
    """List readable files without following escaping symlinks."""

    name = "list_files"
    input_model = ListFilesInput

    def __init__(self, boundary: WorkspaceBoundary) -> None:
        self.boundary = boundary

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(ListFilesInput, arguments)
        files = self.boundary.iter_files(args.path)
        truncated = len(files) > args.max_files
        return ToolObservation(
            summary=f"Listed {min(len(files), args.max_files)} workspace files",
            value={
                "path": args.path,
                "paths": files[: args.max_files],
                "truncated": truncated,
            },
        )


class ReadFileInput(RuntimeModel):
    """Read one optional line range from a workspace file."""

    path: NonEmptyStr
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class ReadFileTool:
    """Read one optional line range through a WorkspaceBoundary."""

    name = "read_file"
    input_model = ReadFileInput
    model_guidance = (
        "If a read result is truncated, search for the symbol then read a "
        "bounded start_line/end_line range; do not repeat the same unbounded read."
    )

    def __init__(
        self,
        boundary: WorkspaceBoundary,
        *,
        max_chars: int = 8_000,
        max_bytes: int = 1_000_000,
    ) -> None:
        self.boundary = boundary
        self.max_chars = max_chars
        self.max_bytes = max_bytes

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(ReadFileInput, arguments)
        if args.start_line and args.end_line and args.end_line < args.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        path = self.boundary.resolve_read_file(args.path)
        if path.stat().st_size > self.max_bytes:
            raise ValueError(f"file is too large to read: {path.stat().st_size} bytes")
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        start = (args.start_line or 1) - 1
        end = args.end_line or len(lines)
        selected = "".join(lines[start:end])
        truncated = len(selected) > self.max_chars
        if truncated:
            selected = selected[: self.max_chars]
        return ToolObservation(
            summary=f"Read {args.path}",
            value={
                "path": args.path,
                "start_line": args.start_line,
                "end_line": args.end_line,
                "content": selected,
                "truncated": truncated,
            },
            memory_updates={"read_paths": _remember(state, "read_paths", args.path)},
        )


class SearchTextInput(RuntimeModel):
    """Case-insensitive bounded text search request."""

    query: NonEmptyStr
    path: str = "."
    max_results: int = Field(default=20, ge=1, le=50)


class SearchTextTool:
    """Search readable text files without invoking a process."""

    name = "search_text"
    input_model = SearchTextInput

    def __init__(self, boundary: WorkspaceBoundary) -> None:
        self.boundary = boundary

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(SearchTextInput, arguments)
        matches: list[dict] = []
        observed: list[str] = []
        for relative in self.boundary.iter_files(args.path):
            path = self.boundary.resolve_read_file(relative)
            if path.stat().st_size > 1_000_000:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            file_matched = False
            for number, line in enumerate(lines, start=1):
                if args.query.lower() not in line.lower():
                    continue
                matches.append({"path": relative, "line": number, "text": line[:200]})
                file_matched = True
                if len(matches) >= args.max_results:
                    break
            if file_matched:
                observed.append(relative)
            if len(matches) >= args.max_results:
                break
        read_paths = list(state.memory.get("read_paths", []))
        for relative in observed:
            if relative not in read_paths:
                read_paths.append(relative)
        return ToolObservation(
            summary=f"Found {len(matches)} matches for {args.query!r}",
            value={"matches": matches, "truncated": len(matches) >= args.max_results},
            memory_updates={"read_paths": read_paths},
        )


class ReadArtifactInput(RuntimeModel):
    """Identify one registered ArtifactRef by id."""

    artifact_id: NonEmptyStr


class ReadArtifactTool:
    """Read a provided ArtifactRef after integrity verification."""

    name = "read_artifact"
    input_model = ReadArtifactInput

    def __init__(self, reader: RegisteredArtifactReader) -> None:
        self.reader = reader

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(ReadArtifactInput, arguments)
        value = self.reader.read_text(args.artifact_id)
        summaries = dict(state.memory.get("read_artifact_summaries", {}))
        summaries[args.artifact_id] = {
            "summary": value["summary"],
            "content": value["content"][:2_000],
        }
        return ToolObservation(
            summary=f"Read registered Artifact {args.artifact_id}",
            value=value,
            memory_updates={
                "read_artifact_ids": _remember(
                    state,
                    "read_artifact_ids",
                    args.artifact_id,
                ),
                "read_artifact_summaries": summaries,
            },
        )


class CreateFileInput(RuntimeModel):
    """Create one new UTF-8 workspace file."""

    path: NonEmptyStr
    content: str


class CreateFileTool:
    """Create a file only when its target does not exist."""

    name = "create_file"
    input_model = CreateFileInput

    def __init__(self, boundary: WorkspaceBoundary) -> None:
        self.boundary = boundary

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(CreateFileInput, arguments)
        path = self.boundary.resolve_write_file(args.path, must_be_new=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            handle.write(args.content)
        revision = int(state.memory.get("edit_revision", 0)) + 1
        return ToolObservation(
            summary=f"Created {args.path}",
            value={"path": args.path, "bytes": len(args.content.encode("utf-8"))},
            memory_updates={"edit_revision": revision},
        )


class ReplaceTextInput(RuntimeModel):
    """Replace one exact text occurrence in an existing file."""

    path: NonEmptyStr
    old_text: str
    new_text: str

    @field_validator("old_text")
    @classmethod
    def _old_text_must_not_be_empty(cls, value: str) -> str:
        """Reject empty matches without stripping whitespace.

        ``old_text`` is an exact-text needle: leading/trailing spaces, tabs and
        newlines are significant (they encode Python indentation). Unlike
        ``NonEmptyStr``, it must never be strip-normalized.
        """
        if value == "":
            raise ValueError("old_text must not be empty")
        return value


class ReplaceTextTool:
    """Atomically apply an exactly-once text replacement."""

    name = "replace_text"
    input_model = ReplaceTextInput

    def __init__(self, boundary: WorkspaceBoundary, *, max_bytes: int = 1_000_000) -> None:
        self.boundary = boundary
        self.max_bytes = max_bytes

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(ReplaceTextInput, arguments)
        path = self.boundary.resolve_write_file(args.path)
        if path.stat().st_size > self.max_bytes:
            raise ValueError(f"file is too large to edit: {path.stat().st_size} bytes")
        text = path.read_text(encoding="utf-8")
        count = text.count(args.old_text)
        if count != 1:
            raise ValueError(f"old_text must match exactly once; found {count}")
        updated = text.replace(args.old_text, args.new_text, 1)
        if updated == text:
            raise ValueError("replacement does not change the file")
        mode = path.stat().st_mode
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                delete=False,
            ) as handle:
                handle.write(updated)
                temporary = Path(handle.name)
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        revision = int(state.memory.get("edit_revision", 0)) + 1
        return ToolObservation(
            summary=f"Replaced one exact match in {args.path}",
            value={"path": args.path},
            memory_updates={"edit_revision": revision},
        )


class RunVerificationInput(RuntimeModel):
    """Agent-chosen shell-free commands, run as one bounded verification pass."""

    commands: list[NonEmptyStr] = Field(min_length=1)


class RunVerificationTool:
    """Run Agent-chosen commands and bind results to the edit revision."""

    name = "run_verification"
    input_model = RunVerificationInput

    def __init__(
        self,
        runner: ProcessRunner,
        repository: GitWorkspace,
        *,
        log_root: str,
        timeout_seconds: int,
        permission_policy: VerificationCommandPolicy | None = None,
        baseline: GitBaseline,
        env_binding: EnvironmentBinding | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.runner = runner
        self.repository = repository
        self.log_root = log_root
        self.timeout_seconds = timeout_seconds
        self.permission_policy = permission_policy or VerificationCommandPolicy()
        self.baseline = baseline
        self.env_binding = env_binding
        self.extra_env = dict(extra_env or {})

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(RunVerificationInput, arguments)
        decision = self.permission_policy.check(args.commands)
        if not decision.allowed:
            raise ValueError(f"verification commands rejected: {decision.reason}")
        argv_prefix = None
        if self.env_binding is not None:
            argv_prefix = self.env_binding.argv_prefix()
            if argv_prefix is None:
                return ToolObservation(
                    summary="No environment prepared; call prepare_environment before verification",
                    ok=False,
                    value={"blocked": True, "reason": "no_environment"},
                )
            if not self.env_binding.certified:
                return ToolObservation(
                    summary="Environment not audited; call audit_env before verification",
                    ok=False,
                    value={"blocked": True, "reason": "not_certified"},
                )
        revision = int(state.memory.get("edit_revision", 0))

        def _digest() -> str:
            diff = self.repository.diff_since(self.baseline)
            return hashlib.sha256(diff.encode("utf-8")).hexdigest()

        before_digest = _digest()
        deadline = monotonic() + self.timeout_seconds
        results: list[VerificationResult] = []
        for index, command in enumerate(args.commands, start=1):
            remaining = deadline - monotonic()
            if remaining <= 0:
                # A command that never ran must still produce a failure record,
                # so a partial verification pass can never be mistaken for
                # success (ADR-0011 §3).
                results.append(
                    VerificationResult(
                        command=command,
                        exit_code=1,
                        timed_out=True,
                        stdout_path=(
                            f"{self.log_root}/revision_{revision}/command_{index:02d}.stdout"
                        ),
                        stderr_path=(
                            f"{self.log_root}/revision_{revision}/command_{index:02d}.stderr"
                        ),
                        duration_seconds=0.0,
                    )
                )
                continue
            results.append(
                self.runner.run(
                    command,
                    log_dir=f"{self.log_root}/revision_{revision}",
                    index=index,
                    timeout_seconds=remaining,
                    argv_prefix=argv_prefix,
                    extra_env=self.extra_env,
                )
            )
        after_digest = _digest()
        workspace_unchanged = before_digest == after_digest
        payload = [result.model_dump(mode="json") for result in results]
        passed = (
            len(results) == len(args.commands)
            and workspace_unchanged
            and all(
                result.exit_code == 0 and not result.timed_out for result in results
            )
        )
        observations = [
            {
                **result.model_dump(mode="json"),
                "stdout_tail": (
                    self.runner.boundary.root / result.stdout_path
                ).read_text(encoding="utf-8", errors="replace")[-2_000:]
                if Path(result.stdout_path).exists()
                else "",
                "stderr_tail": (
                    self.runner.boundary.root / result.stderr_path
                ).read_text(encoding="utf-8", errors="replace")[-2_000:]
                if Path(result.stderr_path).exists()
                else "",
            }
            for result in results
        ]
        return ToolObservation(
            summary=(
                f"Verification {'passed' if passed else 'failed'} at revision {revision}; "
                f"workspace_unchanged={workspace_unchanged}"
            ),
            ok=passed,
            value={
                "passed": passed,
                "workspace_unchanged": workspace_unchanged,
                "results": observations,
            },
            memory_updates={
                "verification_revision": revision,
                "verification_results": payload,
                "verification_diff_sha256": after_digest,
                "verification_workspace_unchanged": workspace_unchanged,
            },
        )


class GitDiffInput(RuntimeModel):
    """Bound the Git diff returned to the Agent context."""

    max_chars: int = Field(default=8_000, ge=1, le=20_000)


class GitDiffTool:
    """Expose the Attempt-relative Git patch (the increment since baseline).

    The Coding Agent always supplies its Attempt baseline, so the Agent sees the
    same increment the deterministic finalizer attributes (ADR-0011 §4).
    """

    name = "git_diff"
    input_model = GitDiffInput

    def __init__(
        self, repository: GitWorkspace, *, baseline: GitBaseline
    ) -> None:
        self.repository = repository
        self.baseline = baseline

    def execute(self, state: AgentState, arguments: BaseModel) -> ToolObservation:
        args = cast(GitDiffInput, arguments)
        diff = self.repository.diff_since(self.baseline)
        truncated = len(diff) > args.max_chars
        return ToolObservation(
            summary="Read current Git diff",
            value={"diff": diff[: args.max_chars], "truncated": truncated},
        )


def media_type_for(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"
