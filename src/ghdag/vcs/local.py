"""ghdag.vcs.local — throwaway bare remote + clone for tests."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from ghdag.vcs.sink import GitSink

__all__ = ["LocalGitSink"]


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class LocalGitSink:
    @staticmethod
    def create(
        tmp_dir: Path,
        *,
        owner: str,
        allow_prefixes: Sequence[str],
        branch: str = "main",
        push: str = "immediate",
        audit_path: Path | None = None,
    ) -> GitSink:
        """Create ``<tmp_dir>/remote.git`` (bare) and ``<tmp_dir>/work`` (clone with one commit).

        Returns a ``GitSink`` rooted at ``work``. Does not consult ``ENABLE_GIT``.
        """
        tmp_dir = Path(tmp_dir)
        remote = tmp_dir / "remote.git"
        work = tmp_dir / "work"
        remote.mkdir(parents=True)
        _git(remote, "init", "--bare", "-q")
        _git(remote, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
        _git(tmp_dir, "clone", "-q", str(remote), str(work))
        _git(work, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
        _git(work, "config", "user.name", "ghdag-test")
        _git(work, "config", "user.email", "ghdag-test@example.invalid")
        _git(work, "config", "commit.gpgsign", "false")
        (work / "README.md").write_text("test repo\n", encoding="utf-8")
        _git(work, "add", "README.md")
        _git(work, "commit", "-q", "-m", "init")
        _git(work, "push", "-q", "origin", f"HEAD:{branch}")
        _git(work, "fetch", "-q", "origin")
        return GitSink(
            work,
            name="local",
            branch=branch,
            owner=owner,
            allow_prefixes=allow_prefixes,
            push=push,
            audit_path=audit_path,
        )
