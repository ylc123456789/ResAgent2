"""Read-only Git observations used by deterministic module finalizers."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .workspace import WorkspaceBoundary


class GitWorkspaceError(ValueError):
    """Raised when a workspace cannot provide an unambiguous Git baseline."""


@dataclass(frozen=True, slots=True)
class GitBaseline:
    """A content snapshot of the working directory at the start of one Attempt.

    ``tree_hash`` is a tree object capturing the tracked working-directory
    state; ``untracked`` maps each untracked path to its content hash at that
    moment (so a later edit to an untracked file is detected, not just its
    first appearance). Comparing later work against this baseline yields only
    the increment produced by the current Attempt, without committing, touching
    the real index, or rolling anything back.
    """

    tree_hash: str
    untracked: dict[str, str]


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
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *arguments],
            cwd=self.boundary.root,
            text=True,
            capture_output=True,
            check=False,
            env=env,
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

    def _untracked_paths(self) -> list[str]:
        return self._run(
            ["ls-files", "--others", "--exclude-standard", "-z"]
        ).stdout.split("\0")

    def _all_changed_paths(self) -> list[str]:
        tracked = self._run(["diff", "--name-only", "-z", "HEAD"]).stdout.split("\0")
        return sorted(
            path
            for path in set([*tracked, *self._untracked_paths()])
            if path and self._visible(path)
        )

    def _write_tree(self) -> str:
        """Return a tree hash of the tracked working-directory state.

        Uses a temporary index (``GIT_INDEX_FILE``) seeded from HEAD, then
        updates only tracked files (``git add -u``); untracked files are kept
        out of the tree and captured separately as ``baseline.untracked``. The
        real index, branch and working tree are untouched.
        """
        fd, tmp_index = tempfile.mkstemp(prefix="resagent2-index-")
        os.close(fd)
        try:
            index_env = {**os.environ, "GIT_INDEX_FILE": tmp_index}
            head = self._run(
                ["rev-parse", "--verify", "HEAD"], accepted=(0, 1), env=index_env
            )
            self._run(
                ["read-tree", "HEAD" if head.returncode == 0 else "--empty"],
                env=index_env,
            )
            self._run(["add", "-u"], env=index_env)
            return self._run(["write-tree"], env=index_env).stdout.strip()
        finally:
            os.unlink(tmp_index)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def snapshot(self) -> GitBaseline:
        """Capture the current working-directory content as an Attempt baseline."""
        untracked: dict[str, str] = {}
        for path in self._untracked_paths():
            if not path or not self._visible(path):
                continue
            full = self.boundary.root / path
            if full.is_file():
                untracked[path] = self._sha256(full)
        return GitBaseline(tree_hash=self._write_tree(), untracked=untracked)

    # ── HEAD-relative observations (legacy; retain for direct callers/tests) ──

    def changed_paths(self) -> list[str]:
        return [
            path for path in self._all_changed_paths() if self.boundary.allows_read(path)
        ]

    def require_clean(self) -> None:
        changed = self._all_changed_paths()
        if changed:
            raise GitWorkspaceError(
                "workspace is not clean; changed paths: " + ", ".join(changed[:10])
            )

    def deleted_paths(self) -> list[str]:
        return [
            path for path in self.changed_paths() if not (self.boundary.root / path).exists()
        ]

    def diff(self) -> str:
        changed = self.changed_paths()
        tracked_paths = set(
            path for path in self._run(["ls-files", "-z"]).stdout.split("\0") if path
        )
        tracked = [path for path in changed if path in tracked_paths]
        chunks: list[str] = []
        if tracked:
            chunks.append(self._run(["diff", "--binary", "HEAD", "--", *tracked]).stdout)
        for relative in changed:
            if relative in tracked_paths or not (self.boundary.root / relative).is_file():
                continue
            result = self._run(
                ["diff", "--no-index", "--binary", "--", os.devnull, relative],
                accepted=(0, 1),
            )
            chunks.append(result.stdout)
        return "".join(chunks)

    # ── Attempt-baseline-relative observations ────────────────────────────────

    def changed_paths_since(self, baseline: GitBaseline) -> list[str]:
        """Return paths whose content differs from ``baseline`` (this Attempt)."""
        tracked = self._run(
            ["diff", "--name-only", "-z", baseline.tree_hash]
        ).stdout.split("\0")
        changed = {path for path in tracked if path}
        for path in self._untracked_paths():
            if not path:
                continue
            full = self.boundary.root / path
            if path not in baseline.untracked:
                changed.add(path)  # newly created untracked file
            elif not full.is_file() or self._sha256(full) != baseline.untracked[path]:
                changed.add(path)  # deleted or modified untracked file
        return sorted(
            path
            for path in changed
            if path and self._visible(path) and self.boundary.allows_read(path)
        )

    def deleted_paths_since(self, baseline: GitBaseline) -> list[str]:
        return [
            path
            for path in self.changed_paths_since(baseline)
            if not (self.boundary.root / path).exists()
        ]

    def diff_since(self, baseline: GitBaseline) -> str:
        """Return the patch produced since ``baseline`` (this Attempt only)."""
        changed = self.changed_paths_since(baseline)
        tracked_in_baseline = set(
            self._run(
                ["ls-tree", "-r", "--name-only", "-z", baseline.tree_hash]
            ).stdout.split("\0")
        )
        tracked = [path for path in changed if path in tracked_in_baseline]
        chunks: list[str] = []
        if tracked:
            chunks.append(
                self._run(["diff", "--binary", baseline.tree_hash, "--", *tracked]).stdout
            )
        for relative in changed:
            if relative in tracked_in_baseline:
                continue
            if not (self.boundary.root / relative).is_file():
                continue
            result = self._run(
                ["diff", "--no-index", "--binary", "--", os.devnull, relative],
                accepted=(0, 1),
            )
            chunks.append(result.stdout)
        return "".join(chunks)

    def write_patch(self, path: Path) -> str:
        """Write the current HEAD-relative diff to an absolute ``path``."""
        patch = self.diff()
        if not patch:
            raise GitWorkspaceError("workspace has no code diff")
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(patch, encoding="utf-8")
        return str(destination)

    def write_patch_since(self, baseline: GitBaseline, path: Path) -> str:
        """Write the Attempt-increment diff to an absolute ``path``."""
        patch = self.diff_since(baseline)
        if not patch:
            raise GitWorkspaceError("workspace has no code diff since the Attempt baseline")
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(patch, encoding="utf-8")
        return str(destination)
