"""Tests for ghdag.vcs.sink.GitSink (nexus Issue #3831, AC-2..AC-4, AC-6)."""

from __future__ import annotations

import json
import multiprocessing
import subprocess
from pathlib import Path

import pytest

from ghdag.vcs import ConflictError, GitSink, LocalGitSink, NullSink, OwnershipError


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


def test_commit_only_includes_given_paths(sink: GitSink, tmp_path: Path) -> None:
    _write(sink, "diary/2026-09-25.md", "entry\n")
    _write(sink, "weekly/2026-W39.md", "unrelated\n")
    (sink.repo_root / "README.md").write_text("dirty\n", encoding="utf-8")

    result = sink.commit(["diary/2026-09-25.md"], "host(diary): 2026-09-25")

    assert result.committed and result.pushed
    names = _git(sink.repo_root, "show", "--name-only", "--format=", "HEAD").split()
    assert names == ["diary/2026-09-25.md"]
    # Unrelated changes survive in the working tree.
    assert (sink.repo_root / "README.md").read_text(encoding="utf-8") == "dirty\n"
    assert (sink.repo_root / "weekly/2026-W39.md").exists()
    assert _remote_head(tmp_path) == result.sha


def test_no_changes_is_skipped(sink: GitSink) -> None:
    _write(sink, "diary/2026-09-25.md", "entry\n")
    sink.commit(["diary/2026-09-25.md"], "host(diary): first")
    before = _count(sink.repo_root)

    result = sink.commit(["diary/2026-09-25.md"], "host(diary): again")

    assert result.skipped and not result.committed and result.reason == "no changes"
    assert _count(sink.repo_root) == before


# --- AC-3: rebase / conflict ---


def test_rebase_onto_remote_commit_on_other_file(sink: GitSink, tmp_path: Path) -> None:
    other = _other_clone(tmp_path)
    _remote_push(other, "weekly/2026-W39.md", "remote\n")

    _write(sink, "diary/2026-09-25.md", "local\n")
    result = sink.commit(["diary/2026-09-25.md"], "host(diary): local")

    assert result.pushed is True
    files = _git(tmp_path / "remote.git", "ls-tree", "-r", "--name-only", "main").split()
    assert "weekly/2026-W39.md" in files
    assert "diary/2026-09-25.md" in files
    assert _remote_head(tmp_path) == result.sha


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


# --- AC-4: concurrent processes ---


def _worker(repo_root: str, rel: str) -> None:
    sink = GitSink(repo_root, name="p", branch="main", owner="host", allow_prefixes=["diary/"])
    target = Path(repo_root) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        target.write_text(f"{rel} {i}\n", encoding="utf-8")
        sink.commit([rel], f"host(diary): {rel} {i}")


def test_concurrent_processes_serialize_via_flock(sink: GitSink, tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(target=_worker, args=(str(sink.repo_root), rel))
        for rel in ("diary/a.md", "diary/b.md")
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    assert [p.exitcode for p in procs] == [0, 0]
    log = _git(sink.repo_root, "log", "--format=%s")
    assert log.count("host(diary): diary/a.md") == 5
    assert log.count("host(diary): diary/b.md") == 5
    assert _remote_head(tmp_path) == _git(sink.repo_root, "rev-parse", "HEAD").strip()


# --- AC-6: audit ---


def _audit_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_audit_records_commit_skip_conflict(sink: GitSink, tmp_path: Path) -> None:
    rel = "diary/2026-09-25.md"
    _write(sink, rel, "base\n")
    sink.commit([rel], "host(diary): base", trailers={"Execution-Id": "e1", "Correlation-Id": "c1"})
    sink.commit([rel], "host(diary): same")
    other = _other_clone(tmp_path)
    _remote_push(other, rel, "remote\n")
    _write(sink, rel, "local\n")
    with pytest.raises(ConflictError):
        sink.commit([rel], "host(diary): conflict")

    events = _audit_events(tmp_path / "audit.jsonl")
    assert [e["event"] for e in events] == ["vcs_commit", "vcs_skipped", "vcs_conflict"]
    for e in events:
        assert e["sink"] == "local"
        assert e["layer"] == "host"
        assert e["host"]
        assert e["paths"] == [rel]
    assert events[0]["execution_id"] == "e1"
    assert events[0]["correlation_id"] == "c1"
    assert events[0]["pushed"] is True


def test_no_audit_without_audit_path(tmp_path: Path) -> None:
    sink = LocalGitSink.create(tmp_path, owner="host", allow_prefixes=["diary/"])
    _write(sink, "diary/a.md", "a\n")
    sink.commit(["diary/a.md"], "host(diary): a")
    assert not list(tmp_path.rglob("audit.jsonl"))


def test_null_sink_writes_vcs_skipped(tmp_path: Path) -> None:
    audit = tmp_path / "audit.jsonl"
    result = NullSink("notes", reason="ENABLE_GIT unset", audit_path=audit).commit(
        ["diary/a.md"], "host(diary): a"
    )
    assert result.skipped and result.reason == "ENABLE_GIT unset"
    (event,) = _audit_events(audit)
    assert event["event"] == "vcs_skipped"
    assert event["sink"] == "notes"
    assert "layer" in event and event["host"]
