"""Physical filesystem boundaries derived from WorkspaceGrant."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

from resagent2_contracts import WorkspaceGrant, WorkspaceMode


class WorkspacePermissionError(PermissionError):
    """Raised before a path can escape or exceed a workspace grant."""


def _normalize_relative(path: str, *, allow_root: bool = False) -> str:
    value = path.strip().replace("\\", "/")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value
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


class WorkspaceBoundary:
    """Resolve read/write paths while enforcing one WorkspaceGrant."""

    reserved_parts = frozenset(
        {
            ".git",
            ".resagent2",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
        }
    )

    def __init__(
        self,
        grant: WorkspaceGrant,
        *,
        write_paths: list[str] | None = None,
    ) -> None:
        self.grant = grant
        self.root = Path(grant.root).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise WorkspacePermissionError("workspace root must be an existing directory")
        self.allowed_paths = tuple(
            _normalize_relative(path, allow_root=True) for path in grant.allowed_paths
        )
        self.denied_paths = tuple(
            _normalize_relative(path, allow_root=True) for path in grant.denied_paths
        )
        self.write_paths = tuple(
            _normalize_relative(path, allow_root=True) for path in (write_paths or [])
        )

    def _check_lexical(self, relative: str, *, system: bool = False) -> None:
        parts = PurePosixPath(relative).parts
        if not system:
            if self.reserved_parts.intersection(parts):
                raise WorkspacePermissionError("path enters a runtime-reserved directory")
            if self.allowed_paths and not _matches(relative, self.allowed_paths):
                raise WorkspacePermissionError("path is outside WorkspaceGrant.allowed_paths")
            if self.denied_paths and _matches(relative, self.denied_paths):
                raise WorkspacePermissionError("path is inside WorkspaceGrant.denied_paths")

    def allows_read(self, relative: str) -> bool:
        """Return whether a relative path is inside the grant's allowed/denied scope."""
        if self.allowed_paths and not _matches(relative, self.allowed_paths):
            return False
        if self.denied_paths and _matches(relative, self.denied_paths):
            return False
        return True

    def _ensure_contained(self, path: Path) -> None:
        if not path.is_relative_to(self.root):
            raise WorkspacePermissionError("resolved path escapes workspace root")

    def resolve_read_file(self, relative_path: str) -> Path:
        relative = _normalize_relative(relative_path)
        self._check_lexical(relative)
        resolved = (self.root / relative).resolve(strict=True)
        self._ensure_contained(resolved)
        if not resolved.is_file():
            raise FileNotFoundError(relative)
        return resolved

    def resolve_read_directory(self, relative_path: str = ".") -> Path:
        relative = _normalize_relative(relative_path, allow_root=True)
        self._check_lexical(relative)
        resolved = (self.root / relative).resolve(strict=True)
        self._ensure_contained(resolved)
        if not resolved.is_dir():
            raise NotADirectoryError(relative)
        return resolved

    def resolve_write_file(self, relative_path: str, *, must_be_new: bool = False) -> Path:
        if self.grant.mode != WorkspaceMode.READ_WRITE:
            raise WorkspacePermissionError("workspace is read-only")
        relative = _normalize_relative(relative_path)
        self._check_lexical(relative)
        if self.write_paths and not _matches(relative, self.write_paths):
            raise WorkspacePermissionError("path is outside CodeModifyInput.allowed_paths")
        candidate = self.root / relative
        if candidate.exists() or candidate.is_symlink():
            resolved = candidate.resolve(strict=True)
            self._ensure_contained(resolved)
            if must_be_new:
                raise FileExistsError(relative)
            if not resolved.is_file():
                raise WorkspacePermissionError("write target must be a file")
            return resolved
        existing = candidate.parent
        while not existing.exists() and existing != self.root:
            existing = existing.parent
        parent = existing.resolve(strict=True)
        self._ensure_contained(parent)
        return candidate

    def resolve_system_write(self, relative_path: str) -> Path:
        """Resolve an agent-controlled path below the reserved .resagent2 root."""
        if self.grant.mode != WorkspaceMode.READ_WRITE:
            raise WorkspacePermissionError("workspace is read-only")
        relative = _normalize_relative(relative_path)
        if not relative.startswith(".resagent2/"):
            raise WorkspacePermissionError("system output must be below .resagent2")
        self._check_lexical(relative, system=True)
        candidate = self.root / relative
        existing = candidate if candidate.exists() else candidate.parent
        while not existing.exists() and existing != self.root:
            existing = existing.parent
        resolved_parent = existing.resolve(strict=True)
        self._ensure_contained(resolved_parent)
        return candidate

    def iter_files(self, relative_path: str = ".") -> list[str]:
        start = self.resolve_read_directory(relative_path)
        files: list[str] = []
        for current, directories, names in os.walk(start, followlinks=False):
            current_path = Path(current)
            kept: list[str] = []
            for name in directories:
                relative = (current_path / name).relative_to(self.root).as_posix()
                try:
                    self._check_lexical(relative)
                    resolved = (current_path / name).resolve(strict=True)
                    self._ensure_contained(resolved)
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
