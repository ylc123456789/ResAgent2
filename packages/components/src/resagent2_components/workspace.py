"""Physical filesystem boundaries derived from WorkspaceGrant."""

from __future__ import annotations

import hashlib
import os
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from resagent2_contracts import WorkspaceGrant


class WorkspacePermissionError(PermissionError):
    """Raised before a path can escape or exceed a workspace grant."""


class DeleteChangedError(WorkspacePermissionError):
    """A prepared deletion no longer describes the filesystem."""


class DeleteInterruptedError(OSError):
    """A deletion stopped after making partial progress."""

    def __init__(self, reason: str, deleted: list[str], remaining: list[str]) -> None:
        super().__init__(reason)
        self.deleted = deleted
        self.remaining = remaining


def _normalize_relative(path: str, *, allow_root: bool = False) -> str:
    value = path.strip().replace("\\", "/")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value
        or "\x00" in value
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix.parts
    ):
        raise WorkspacePermissionError("path must be relative and cannot contain '..'")
    normalized = posix.as_posix()
    if normalized == "." and not allow_root:
        raise WorkspacePermissionError("path must identify a workspace entry")
    return normalized


def _matches(relative: str, prefixes: tuple[str, ...]) -> bool:
    return any(
        prefix == "." or relative == prefix or relative.startswith(f"{prefix}/")
        for prefix in prefixes
    )


def _version(info: os.stat_result) -> list[int]:
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns]


class WorkspaceBoundary:
    """Resolve paths while enforcing one read/write/exclusion scope."""

    reserved_parts = frozenset({".git", ".resagent2"})
    ignored_parts = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})

    def __init__(self, grant: WorkspaceGrant) -> None:
        self.grant = grant
        self.root = Path(grant.root).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise WorkspacePermissionError("workspace root must be an existing directory")
        self.read_paths = tuple(grant.access.read_paths)
        self.write_paths = tuple(grant.access.write_paths)
        self.denied_paths = tuple(grant.access.denied_paths)

    def _check_lexical(self, relative: str, *, write: bool = False, traverse: bool = False) -> None:
        if self.reserved_parts.intersection(PurePosixPath(relative).parts):
            raise WorkspacePermissionError("path enters a runtime-reserved directory")
        if _matches(relative, self.denied_paths):
            raise WorkspacePermissionError("path is inside WorkspaceAccess.denied_paths")
        scope = self.write_paths if write else self.read_paths
        if _matches(relative, scope):
            return
        if traverse and any(_matches(prefix, (relative,)) for prefix in scope):
            return
        kind = "write" if write else "read"
        raise WorkspacePermissionError(f"path is outside the workspace {kind} scope")

    def allows_read(self, relative: str) -> bool:
        """Return whether a relative path is readable under the complete scope."""
        try:
            self._check_lexical(_normalize_relative(relative, allow_root=True))
        except WorkspacePermissionError:
            return False
        return True

    def _ensure_contained(self, path: Path) -> None:
        if not path.is_relative_to(self.root):
            raise WorkspacePermissionError("resolved path escapes workspace root")

    def _check_resolved(self, resolved: Path, *, write: bool = False, traverse: bool = False) -> None:
        self._ensure_contained(resolved)
        relative = resolved.relative_to(self.root).as_posix()
        self._check_lexical(relative, write=write, traverse=traverse)

    def resolve_read_file(self, relative_path: str) -> Path:
        relative = _normalize_relative(relative_path)
        self._check_lexical(relative)
        resolved = (self.root / relative).resolve(strict=True)
        self._ensure_contained(resolved)
        self._check_resolved(resolved)
        if not resolved.is_file():
            raise FileNotFoundError(relative)
        return resolved

    def resolve_read_directory(self, relative_path: str = ".", *, traverse: bool = False) -> Path:
        relative = _normalize_relative(relative_path, allow_root=True)
        self._check_lexical(relative, traverse=traverse)
        resolved = (self.root / relative).resolve(strict=True)
        self._ensure_contained(resolved)
        self._check_resolved(resolved, traverse=traverse)
        if not resolved.is_dir():
            raise NotADirectoryError(relative)
        return resolved

    def resolve_write_file(self, relative_path: str, *, must_be_new: bool = False) -> Path:
        relative = _normalize_relative(relative_path)
        self._check_lexical(relative, write=True)
        candidate = self.root / relative
        resolved = candidate.resolve(strict=False)
        self._check_resolved(resolved, write=True)
        if candidate.exists() or candidate.is_symlink():
            if must_be_new:
                raise FileExistsError(relative)
            if not resolved.is_file():
                raise WorkspacePermissionError("write target must be a file")
        return resolved

    def resolve_system_write(self, relative_path: str) -> Path:
        """Resolve trusted framework output, separately from model file grants."""
        relative = _normalize_relative(relative_path)
        if not relative.startswith(".resagent2/"):
            raise WorkspacePermissionError("system output must be below .resagent2")
        candidate = (self.root / relative).resolve(strict=False)
        self._ensure_contained(candidate)
        if not candidate.is_relative_to(self.root / ".resagent2"):
            raise WorkspacePermissionError("system output escapes .resagent2")
        return candidate

    def iter_files(self, relative_path: str = ".") -> list[str]:
        start = self.resolve_read_directory(relative_path, traverse=True)
        files: list[str] = []
        for current, directories, names in os.walk(start, followlinks=False):
            current_path = Path(current)
            kept: list[str] = []
            for name in directories:
                if name in self.ignored_parts:
                    continue
                relative = (current_path / name).relative_to(self.root).as_posix()
                try:
                    self.resolve_read_directory(relative, traverse=True)
                except (OSError, WorkspacePermissionError):
                    continue
                kept.append(name)
            directories[:] = kept
            for name in names:
                relative = (current_path / name).relative_to(self.root).as_posix()
                try:
                    self.resolve_read_file(relative)
                except (OSError, WorkspacePermissionError):
                    continue
                files.append(relative)
        return sorted(files)

    def relative(self, path: Path) -> str:
        resolved = path.resolve(strict=True)
        self._ensure_contained(resolved)
        return resolved.relative_to(self.root).as_posix()

    def _delete_target(self, relative_path: str) -> Path:
        relative = _normalize_relative(relative_path)
        self._check_lexical(relative, write=True)
        lexical = self.root / relative
        parent = lexical.parent.resolve(strict=True)
        self._ensure_contained(parent)
        target = parent / lexical.name
        self._check_resolved(target, write=True)
        return target

    def prepare_delete(self, relative_path: str, *, recursive: bool = False) -> dict[str, Any]:
        """Snapshot exact entries without following the final symlink."""
        relative = _normalize_relative(relative_path)
        entries: list[dict[str, Any]] = []

        def visit(path: str) -> None:
            target = self._delete_target(path)
            info = target.lstat()
            if stat.S_ISLNK(info.st_mode):
                kind = "symlink"
            elif stat.S_ISREG(info.st_mode):
                kind = "file"
            elif stat.S_ISDIR(info.st_mode):
                kind = "directory"
            else:
                raise WorkspacePermissionError("delete target must be a file, directory or symlink")
            entry = {
                "path": path,
                "resolved_path": target.relative_to(self.root).as_posix(),
                "kind": kind,
                "version": _version(info),
            }
            if kind == "file":
                with self._delete_parent(target) as parent:
                    descriptor = os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                    with os.fdopen(descriptor, "rb") as handle:
                        entry["sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
                        if _version(os.fstat(handle.fileno())) != entry["version"]:
                            raise DeleteChangedError("delete target changed while preparing its snapshot")
            entries.append(entry)
            if kind == "directory":
                children = sorted(target.iterdir(), key=lambda child: child.name)
                if children and not recursive:
                    raise WorkspacePermissionError("nonempty directory deletion requires recursive=True")
                for child in children:
                    visit(f"{path}/{child.name}")
                if _version(target.lstat()) != _version(info):
                    raise DeleteChangedError("delete target changed while preparing its snapshot")

        visit(relative)
        return {
            "path": relative,
            "recursive": recursive,
            "requires_confirmation": len(entries) > 1,
            "entries": entries,
        }

    @contextmanager
    def _delete_parent(self, target: Path):
        # Anchor unlink/rmdir to directories opened without following replacement links.
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(self.root, flags)
        try:
            for part in target.parent.relative_to(self.root).parts:
                child = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)

    def delete_prepared(self, snapshot: dict[str, Any]) -> list[str]:
        """Delete only a previously inspected, unchanged and still authorized set."""
        current = self.prepare_delete(snapshot["path"], recursive=snapshot["recursive"])
        if current != snapshot:
            raise DeleteChangedError("delete target changed; prepare and confirm the new snapshot")
        pending = list(reversed(current["entries"]))
        deleted: list[str] = []
        try:
            for entry in pending:
                target = self._delete_target(entry["path"])
                if target.relative_to(self.root).as_posix() != entry["resolved_path"]:
                    raise DeleteChangedError("delete target parent changed during execution")
                with self._delete_parent(target) as parent:
                    actual = _version(os.stat(target.name, dir_fd=parent, follow_symlinks=False))
                    expected = entry["version"]
                    # Child removal changes directory times, but not its identity or mode.
                    if entry["kind"] == "directory":
                        changed = actual[:3] != expected[:3]
                    else:
                        changed = actual != expected
                    if changed:
                        raise DeleteChangedError("delete target changed during execution")
                    if entry["kind"] == "directory":
                        os.rmdir(target.name, dir_fd=parent)
                    else:
                        os.unlink(target.name, dir_fd=parent)
                deleted.append(entry["path"])
        except OSError as error:
            if not deleted:
                raise
            remaining = [entry["path"] for entry in pending[len(deleted):]]
            raise DeleteInterruptedError(str(error), deleted, remaining) from error
        return deleted
