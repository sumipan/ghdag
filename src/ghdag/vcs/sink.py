"""ghdag.vcs.sink — one-shot git sink (stage -> commit -> fetch -> rebase -> push).

A ``GitSink`` owns a set of path prefixes inside one repository and commits only
the paths it is given. All sinks sharing a repository serialize through an
``flock`` on ``<git-common-dir>/ghdag-vcs.lock`` (across processes as well).
"""

from __future__ import annotations

import fcntl
import os
import socket
import subprocess
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ghdag.io.audit import append_audit_record, now_ts

__all__ = [
    "CommitResult",
    "ConflictError",
    "GitSink",
    "NullSink",
    "OwnershipError",
]

_LOCK_NAME = "ghdag-vcs.lock"
_LAST_PUSH_NAME = "ghdag-vcs-last-push"
_MAX_PUSH_RETRIES = 2
# Variables that would redirect git away from repo_root (e.g. when run from a hook).
_GIT_ENV_STRIP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")


@dataclass(frozen=True)
class CommitResult:
    committed: bool
    pushed: bool
    skipped: bool = False
    reason: str | None = None
    sha: str | None = None
    paths: tuple[str, ...] = ()


class OwnershipError(ValueError):
    """A path is outside ``allow_prefixes`` or the subject does not start with ``<owner>(``."""


class ConflictError(RuntimeError):
    """Rebase onto the remote conflicted; local versions were saved under ``inbox/``."""

    def __init__(self, message: str, inbox_paths: Sequence[Path] = ()) -> None:
        super().__init__(message)
        self.inbox_paths: tuple[Path, ...] = tuple(inbox_paths)


def _parse_push(push: str) -> tuple[str, float]:
    if push in ("immediate", "manual"):
        return push, 0.0
    if push.startswith("debounce:"):
        raw = push.split(":", 1)[1]
        try:
            sec = float(raw)
        except ValueError:
            raise ValueError(f"invalid push policy: {push!r}") from None
        if sec < 0:
            raise ValueError(f"invalid push policy: {push!r}")
        return "debounce", sec
    raise ValueError(f"invalid push policy: {push!r} (expected immediate | debounce:<sec> | manual)")


def _first_line(text: str) -> str:
    for line in text.strip().splitlines():
        if line.strip():
            return line.strip()
    return ""


class NullSink:
    """Sink that never touches git; every call returns ``skipped=True``."""

    def __init__(self, name: str, *, reason: str, audit_path: Path | None = None) -> None:
        self.name = name
        self.reason = reason
        self.audit_path = Path(audit_path) if audit_path else None

    def commit(
        self,
        paths: Sequence[str],
        message: str,
        *,
        trailers: Mapping[str, str] | None = None,
    ) -> CommitResult:
        result = CommitResult(
            committed=False, pushed=False, skipped=True, reason=self.reason, paths=tuple(paths)
        )
        if self.audit_path is not None:
            trailers = trailers or {}
            append_audit_record(
                self.audit_path,
                {
                    "event": "vcs_skipped",
                    "timestamp": now_ts(),
                    "sink": self.name,
                    "owner": None,
                    "layer": None,
                    "host": socket.gethostname(),
                    "paths": list(result.paths),
                    "sha": None,
                    "pushed": False,
                    "reason": self.reason,
                    "execution_id": trailers.get("Execution-Id"),
                    "correlation_id": trailers.get("Correlation-Id"),
                },
            )
        return result

    def flush(self) -> CommitResult:
        return CommitResult(committed=False, pushed=False, skipped=True, reason=self.reason)


class GitSink:
    """Commit a fixed set of owned paths to one repository and sync with its remote."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        name: str,
        branch: str,
        owner: str,
        allow_prefixes: Sequence[str],
        remote: str = "origin",
        push: str = "immediate",
        layer: str | None = None,
        audit_path: Path | None = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.name = name
        self.branch = branch
        self.owner = owner
        self.allow_prefixes = tuple(allow_prefixes)
        self.remote = remote
        self.push = push
        self._push_mode, self._debounce_sec = _parse_push(push)
        self.layer = layer or owner
        self.audit_path = Path(audit_path) if audit_path else None
        self._host = socket.gethostname()

    # --- public API ---

    def commit(
        self,
        paths: Sequence[str],
        message: str,
        *,
        trailers: Mapping[str, str] | None = None,
    ) -> CommitResult:
        paths_t = tuple(paths)
        self._check_ownership(paths_t, message)
        trailers = dict(trailers or {})
        with self._locked():
            self._git("add", "--", *paths_t)
            if self._git("diff", "--cached", "--quiet", "--", *paths_t, check=False).returncode == 0:
                result = CommitResult(
                    committed=False, pushed=False, skipped=True, reason="no changes", paths=paths_t
                )
                self._audit("vcs_skipped", result, trailers)
                return result

            self._git("commit", "-m", message, "-m", self._trailer_block(trailers), "--", *paths_t)
            sha = self._head()

            if self._push_mode == "manual":
                result = CommitResult(committed=True, pushed=False, sha=sha, paths=paths_t)
            elif self._push_mode == "debounce" and not self._debounce_elapsed():
                result = CommitResult(
                    committed=True, pushed=False, reason="push deferred", sha=sha, paths=paths_t
                )
            else:
                try:
                    pushed, reason = self._sync()
                except ConflictError as exc:
                    conflict = CommitResult(
                        committed=True, pushed=False, reason=str(exc), sha=sha, paths=paths_t
                    )
                    self._audit("vcs_conflict", conflict, trailers)
                    raise
                result = CommitResult(
                    committed=True, pushed=pushed, reason=reason, sha=self._head(), paths=paths_t
                )
            self._audit("vcs_commit", result, trailers)
            return result

    def flush(self) -> CommitResult:
        """Push commits that were left unpushed by ``manual`` / ``debounce`` policies."""
        with self._locked():
            ahead = self._git(
                "rev-list", "--count", f"{self.remote}/{self.branch}..HEAD", check=False
            )
            if ahead.returncode == 0 and ahead.stdout.strip() == "0":
                return CommitResult(committed=False, pushed=False, skipped=True, reason="no changes")
            try:
                pushed, reason = self._sync()
            except ConflictError as exc:
                self._audit(
                    "vcs_conflict",
                    CommitResult(committed=False, pushed=False, reason=str(exc)),
                    {},
                )
                raise
            return CommitResult(committed=False, pushed=pushed, reason=reason, sha=self._head())

    # --- validation ---

    def _check_ownership(self, paths: tuple[str, ...], message: str) -> None:
        if not paths:
            raise OwnershipError("no paths given")
        for p in paths:
            parts = Path(p).parts
            if Path(p).is_absolute() or ".." in parts:
                raise OwnershipError(f"path must be relative to repo_root without '..': {p!r}")
            if not any(p.startswith(prefix) for prefix in self.allow_prefixes):
                raise OwnershipError(
                    f"path {p!r} is outside allow_prefixes {list(self.allow_prefixes)} of sink {self.name!r}"
                )
        subject = message.splitlines()[0] if message else ""
        if not subject.startswith(f"{self.owner}("):
            raise OwnershipError(f"commit subject must start with {self.owner + '('!r}: {subject!r}")

    # --- git plumbing ---

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV_STRIP}
        proc = subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            env=env,
            capture_output=True,
            text=True,
        )
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args[:2])} failed ({proc.returncode}): {_first_line(proc.stderr)}"
            )
        return proc

    def _head(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.strip()

    def _common_dir(self) -> Path:
        raw = self._git("rev-parse", "--git-common-dir").stdout.strip()
        path = Path(raw)
        return path if path.is_absolute() else (self.repo_root / path).resolve()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock_path = self._common_dir() / _LOCK_NAME
        with open(lock_path, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _trailer_block(self, trailers: Mapping[str, str]) -> str:
        lines = [f"Layer: {self.layer}", f"Host: {self._host}"]
        lines += [f"{k}: {v}" for k, v in trailers.items() if k not in ("Layer", "Host")]
        return "\n".join(lines)

    def _debounce_elapsed(self) -> bool:
        stamp = self._common_dir() / _LAST_PUSH_NAME
        try:
            last = float(stamp.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return True
        return time.time() - last >= self._debounce_sec

    def _record_push(self) -> None:
        (self._common_dir() / _LAST_PUSH_NAME).write_text(f"{time.time():.3f}\n", encoding="utf-8")

    def _sync(self) -> tuple[bool, str | None]:
        """fetch -> rebase -> push, retrying non-fast-forward rejections.

        Returns ``(pushed, reason)``. Raises ``ConflictError`` on rebase conflicts.
        """
        upstream = f"{self.remote}/{self.branch}"
        reason: str | None = None
        for _ in range(_MAX_PUSH_RETRIES + 1):
            fetch = self._git("fetch", self.remote, self.branch, check=False)
            if fetch.returncode != 0:
                return False, f"push failed: {_first_line(fetch.stderr)}"
            rebase = self._git("rebase", "--autostash", upstream, check=False)
            if rebase.returncode != 0:
                self._handle_rebase_failure(upstream, rebase)
            push = self._git("push", self.remote, f"HEAD:{self.branch}", check=False)
            if push.returncode == 0:
                self._record_push()
                return True, None
            reason = f"push failed: {_first_line(push.stderr)}"
            stderr = push.stderr.lower()
            if not ("non-fast-forward" in stderr or "fetch first" in stderr or "[rejected]" in stderr):
                return False, reason
        return False, reason

    def _handle_rebase_failure(self, upstream: str, rebase: subprocess.CompletedProcess[str]) -> None:
        if self._git("rebase", "--abort", check=False).returncode != 0:
            # No rebase in progress: the rebase refused to start (not a content conflict).
            raise RuntimeError(f"git rebase failed: {_first_line(rebase.stderr or rebase.stdout)}")
        changed = self._git("diff", "--name-only", f"{upstream}...HEAD").stdout.split()
        inbox = self.repo_root / "inbox"
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        saved: list[Path] = []
        for rel in changed:
            show = subprocess.run(
                ["git", "show", f"HEAD:{rel}"],
                cwd=self.repo_root,
                env={k: v for k, v in os.environ.items() if k not in _GIT_ENV_STRIP},
                capture_output=True,
            )
            if show.returncode != 0:
                continue  # deleted in HEAD: nothing to save
            inbox.mkdir(parents=True, exist_ok=True)
            dest = inbox / f"{stamp}-{rel.replace('/', '__')}"
            dest.write_bytes(show.stdout)
            saved.append(dest)
        self._git("reset", "--keep", upstream)
        raise ConflictError(
            f"rebase onto {upstream} conflicted; saved {len(saved)} file(s) to inbox/",
            inbox_paths=saved,
        )

    def _audit(self, event: str, result: CommitResult, trailers: Mapping[str, str]) -> None:
        if self.audit_path is None:
            return
        append_audit_record(
            self.audit_path,
            {
                "event": event,
                "timestamp": now_ts(),
                "sink": self.name,
                "owner": self.owner,
                "layer": self.layer,
                "host": self._host,
                "paths": list(result.paths),
                "sha": result.sha,
                "pushed": result.pushed,
                "reason": result.reason,
                "execution_id": trailers.get("Execution-Id"),
                "correlation_id": trailers.get("Correlation-Id"),
            },
        )
