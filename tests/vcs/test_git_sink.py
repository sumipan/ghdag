"""Tests for ghdag.vcs.sink.GitSink (nexus Issue #3831, AC-2..AC-4, AC-6)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ghdag.vcs import ConflictError, LocalGitSink, OwnershipError
from ghdag.vcs.sink import GitSink


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def _other_clone(tmp_path: Path, name: str = "other") -> Path:
    dest = tmp_path / name
    _git(tmp_path, "clone", "-q", str(tmp_path / "remote.git"), str(dest))
    _git(dest, "config", "user.name", "other")
    _git(dest, "config", "user.email", "other@example.invalid")
    _git(dest, "config", "commit.gpgsign", "false")
    return dest


def _remote_push(clone: Path, rel: str, content: str, msg: str = "other(x): remote") -> None:
    target = clone / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(clone, "add", rel)
    _git(clone, "commit", "-q", "-m", msg)
    _git(clone, "push", "-q", "origin", "HEAD:main")


def _remote_head(tmp_path: Path) -> str:
    return _git(tmp_path / "remote.git", "rev-parse", "main").strip()


def _write(sink: GitSink, rel: str, content: str) -> None:
    target = sink.repo_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _count(repo: Path) -> int:
    return int(_git(repo, "rev-list", "--count", "HEAD").strip())


@pytest.fixture
def sink(tmp_path: Path) -> GitSink:
    return LocalGitSink.create(
        tmp_path, owner="host", allow_prefixes=["diary/", "weekly/"], audit_path=tmp_path / "audit.jsonl"
    )


# --- AC-2: ownership ---


@pytest.mark.parametrize(
    ("paths", "message"),
    [
        (["notes/2026-09-25.md"], "host(diary): add"),
        (["diary/2026-09-25.md"], "mltgnt(diary): add"),
        (["diary/../notes/x.md"], "host(diary): add"),
    ],
)
def test_ownership_error_leaves_repo_untouched(sink: GitSink, paths: list[str], message: str) -> None:
    _write(sink, "diary/2026-09-25.md", "a\n")
    _write(sink, "notes/2026-09-25.md", "b\n")
    before_status = _git(sink.repo_root, "status", "--porcelain")
    before_head = _git(sink.repo_root, "rev-parse", "HEAD")
    with pytest.raises(OwnershipError):
        sink.commit(paths, message)
    assert _git(sink.repo_root, "status", "--porcelain") == before_status
    assert _git(sink.repo_root, "rev-parse", "HEAD") == before_head


# --- AC-2b: only the given paths ---


def test_no_changes_is_skipped(sink: GitSink) -> None:
    _write(sink, "diary/2026-09-25.md", "entry\n")
    sink.commit(["diary/2026-09-25.md"], "host(diary): first")
    before = _count(sink.repo_root)

    result = sink.commit(["diary/2026-09-25.md"], "host(diary): again")

    assert result.skipped and not result.committed and result.reason == "no changes"
    assert _count(sink.repo_root) == before


# --- AC-3: rebase / conflict ---


def test_conflict_saves_local_version_to_inbox(sink: GitSink, tmp_path: Path) -> None:
    rel = "diary/2026-09-25.md"
    _write(sink, rel, "base\n")
    sink.commit([rel], "host(diary): base")

    other = _other_clone(tmp_path)
    _remote_push(other, rel, "remote version\n")

    _write(sink, rel, "local version\n")
    with pytest.raises(ConflictError) as excinfo:
        sink.commit([rel], "host(diary): local edit")

    work = sink.repo_root
    assert _git(work, "status", "--porcelain", "--untracked-files=no") == ""
    assert _git(work, "rev-parse", "HEAD").strip() == _remote_head(tmp_path)
    inbox = sorted((work / "inbox").iterdir())
    assert len(inbox) == 1
    assert inbox[0].name.endswith("-diary__2026-09-25.md")
    assert inbox[0].read_text(encoding="utf-8") == "local version\n"
    assert excinfo.value.inbox_paths == (inbox[0],)


# --- AC-3b: trailers ---


def test_trailers_in_commit_message(sink: GitSink) -> None:
    _write(sink, "diary/2026-09-25.md", "entry\n")
    sink.commit(["diary/2026-09-25.md"], "host(diary): t", trailers={"Execution-Id": "exec-123"})
    body = _git(sink.repo_root, "log", "-1", "--format=%B")
    assert "Layer: host" in body
    assert "Host: " in body
    assert "Execution-Id: exec-123" in body


# --- AC-3c: push policies ---


def test_manual_push_waits_for_flush(tmp_path: Path) -> None:
    sink = LocalGitSink.create(tmp_path, owner="host", allow_prefixes=["diary/"], push="manual")
    before = _remote_head(tmp_path)
    _write(sink, "diary/2026-09-25.md", "entry\n")

    result = sink.commit(["diary/2026-09-25.md"], "host(diary): manual")

    assert result.committed and result.pushed is False
    assert _remote_head(tmp_path) == before
    flushed = sink.flush()
    assert flushed.pushed is True
    assert _remote_head(tmp_path) == _git(sink.repo_root, "rev-parse", "HEAD").strip()


def test_debounce_defers_second_push(tmp_path: Path) -> None:
    sink = LocalGitSink.create(tmp_path, owner="host", allow_prefixes=["diary/"], push="debounce:3600")
    _write(sink, "diary/a.md", "a\n")
    first = sink.commit(["diary/a.md"], "host(diary): a")
    _write(sink, "diary/b.md", "b\n")
    second = sink.commit(["diary/b.md"], "host(diary): b")

    assert first.pushed is True
    assert second.committed and second.pushed is False
    assert second.reason == "push deferred"
    assert sink.flush().pushed is True


def test_bogus_push_policy_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        GitSink(tmp_path, name="x", branch="main", owner="host", allow_prefixes=["diary/"], push="bogus")




