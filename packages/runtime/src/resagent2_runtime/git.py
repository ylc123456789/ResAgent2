"""Read-only Git observations used by deterministic module finalizers."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .workspace import WorkspaceBoundary


class GitWorkspaceError(ValueError):
    """Raised when a workspace cannot provide an unambiguous Git baseline."""


class GitWorkspace:
    """Observe one existing repository without mutating its Git state."""

    def __init__(self, boundary: WorkspaceBoundary) -> None:
        self.boundary = boundary
        top = self._run(["rev-parse", "--show-toplevel"]).stdout.strip()
        if Path(top).resolve() != boundary.root:
            raise GitWorkspaceError("workspace root must be the Git repository root")

    def _run(
        self,
        arguments: list[str],
        *,
        accepted: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *arguments],
            cwd=self.boundary.root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode not in accepted:
            raise GitWorkspaceError(result.stderr.strip() or "git command failed")
        return result

    @staticmethod
    def _visible(path: str) -> bool:
        ignored_parts = {
            ".resagent2",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
        }
        return not ignored_parts.intersection(Path(path).parts)

    def _all_changed_paths(self) -> list[str]:
        tracked = self._run(["diff", "--name-only", "-z", "HEAD"]).stdout.split("\0")
        untracked = self._run(
            ["ls-files", "--others", "--exclude-standard", "-z"]
        ).stdout.split("\0")
        return sorted(
            path for path in set([*tracked, *untracked]) if path and self._visible(path)
        )

    def changed_paths(self) -> list[str]:
        return [
            path for path in self._all_changed_paths() if self.boundary.allows_read(path)
        ]

    def require_clean(self) -> None:
        changed = self._all_changed_paths()
        if changed:
            raise GitWorkspaceError(
                "native Coding Agent requires a clean Git workspace; changed paths: "
                + ", ".join(changed[:10])
            )

    def deleted_paths(self) -> list[str]:
        return [
            path for path in self.changed_paths() if not (self.boundary.root / path).exists()
        ]

    def diff(self) -> str:
        changed = self.changed_paths()
        tracked_paths = set(
            path
            for path in self._run(["ls-files", "-z"]).stdout.split("\0")
            if path
        )
        tracked = [path for path in changed if path in tracked_paths]
        chunks: list[str] = []
        if tracked:
            chunks.append(
                self._run(["diff", "--binary", "HEAD", "--", *tracked]).stdout
            )
        for relative in changed:
            if relative in tracked_paths or not (self.boundary.root / relative).is_file():
                continue
            result = self._run(
                ["diff", "--no-index", "--binary", "--", os.devnull, relative],
                accepted=(0, 1),
            )
            chunks.append(result.stdout)
        return "".join(chunks)

    def write_patch(self, relative_path: str) -> str:
        patch = self.diff()
        if not patch:
            raise GitWorkspaceError("workspace has no code diff")
        destination = self.boundary.resolve_system_write(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(patch, encoding="utf-8")
        return relative_path
