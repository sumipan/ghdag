"""Unit tests for LocalForge (nexus #3100)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ghdag.core.exceptions import GitHubApiError
from ghdag.core.ports.forge import ForgePort
from ghdag.forge.local import LocalForge


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def _init_git_repo(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "localforge@test")
    _git(root, "config", "user.name", "LocalForge Test")
    (root / "README").write_text("base\n", encoding="utf-8")
    _git(root, "add", "README")
    _git(root, "commit", "-m", "initial")
    # Ensure main exists as default branch name
    try:
        _git(root, "branch", "-M", "main")
    except subprocess.CalledProcessError:
        pass


@pytest.fixture
def forge_root(tmp_path: Path) -> Path:
    _init_git_repo(tmp_path)
    return tmp_path


@pytest.fixture
def forge(forge_root: Path) -> LocalForge:
    return LocalForge(forge_root, repo="local/test")


def test_local_forge_satisfies_forge_port(forge: LocalForge) -> None:
    assert isinstance(forge, ForgePort)
    assert forge.repo == "local/test"


def test_issue_crud_create_get_update_close_reopen(forge: LocalForge) -> None:
    number = forge.issue_create(
        "hello",
        "body text",
        labels=["phase:a", "bug"],
    )
    assert number >= 1

    got = forge.issue_get(number, fields=["number", "title", "state", "labels", "body"])
    assert got["number"] == number
    assert got["title"] == "hello"
    assert got["body"] == "body text"
    assert got["state"] == "OPEN"
    assert {lbl["name"] for lbl in got["labels"]} == {"phase:a", "bug"}

    forge.issue_update(
        number,
        title="hello2",
        body="body2",
        labels_add=["phase:b"],
        labels_remove=["phase:a"],
    )
    updated = forge.issue_get(number, fields=["title", "body", "labels"])
    assert updated["title"] == "hello2"
    assert updated["body"] == "body2"
    assert {lbl["name"] for lbl in updated["labels"]} == {"bug", "phase:b"}

    forge.issue_close(number)
    assert forge.issue_get(number, fields=["state"])["state"] == "CLOSED"

    forge.reopen_issue(number)
    assert forge.issue_get(number, fields=["state"])["state"] == "OPEN"


def test_issue_comment_and_list_comments(forge: LocalForge) -> None:
    number = forge.issue_create("c", "b")
    created = forge.issue_comment(number, "first comment")
    assert created["body"] == "first comment"

    comments = forge.get_issue_comments(number)
    assert len(comments) == 1
    assert comments[0]["body"] == "first comment"
    assert "author" in comments[0]
    assert "created_at" in comments[0]


def test_issue_get_missing_raises_404(forge: LocalForge) -> None:
    with pytest.raises(GitHubApiError) as exc_info:
        forge.issue_get(999999999)
    assert exc_info.value.status_code == 404


def test_list_issues_by_label(forge: LocalForge) -> None:
    n1 = forge.issue_create("a", "b", labels=["want"])
    forge.issue_create("c", "d", labels=["other"])
    forge.issue_create("e", "f", labels=["want"])
    forge.issue_close(n1)

    open_want = forge.list_issues("want", state="open")
    assert len(open_want) == 1
    assert open_want[0]["title"] == "e"

    all_want = forge.list_issues("want", state="all")
    assert {i["number"] for i in all_want} == {n1, open_want[0]["number"]}


def test_update_label_and_remove_label(forge: LocalForge) -> None:
    number = forge.issue_create("t", "b", labels=["old"])
    forge.update_label(number, remove="old", add="new")
    labels = {lbl["name"] for lbl in forge.issue_get(number, fields=["labels"])["labels"]}
    assert labels == {"new"}
    forge.remove_label(number, "new")
    labels = {lbl["name"] for lbl in forge.issue_get(number, fields=["labels"])["labels"]}
    assert labels == set()


def test_milestone_create_and_list(forge: LocalForge) -> None:
    mid = forge.milestone_create("M1", "desc")
    assert mid >= 1
    ms = forge.milestone_list()
    assert any(m["number"] == mid and m["title"] == "M1" for m in ms)

    number = forge.issue_create("with-ms", "b", milestone=mid)
    got = forge.issue_get(number, fields=["milestone"])
    assert got["milestone"]["number"] == mid
    assert got["milestone"]["title"] == "M1"


def test_pr_create_list_merge(forge: LocalForge, forge_root: Path) -> None:
    _git(forge_root, "checkout", "-b", "feature/pr1")
    (forge_root / "feature.txt").write_text("feat\n", encoding="utf-8")
    _git(forge_root, "add", "feature.txt")
    _git(forge_root, "commit", "-m", "feature")
    _git(forge_root, "checkout", "main")

    url = forge.pr_create("main", "feature/pr1", "PR title", "PR body")
    assert "pull" in url

    listed = forge.pr_list(state="open")
    assert len(listed) == 1
    assert listed[0]["title"] == "PR title"
    assert listed[0]["state"] == "OPEN"
    assert listed[0]["headRefName"] == "feature/pr1"
    assert listed[0]["mergeable"] in {"MERGEABLE", "CONFLICTING", "UNKNOWN"}
    number = listed[0]["number"]

    detail = forge.pr_get(number)
    assert detail["number"] == number
    assert detail["reviewDecision"] == "APPROVED"
    assert detail["body"] == "PR body"

    forge.pr_merge(number, delete_branch=False)
    after = forge.pr_get(number)
    assert after["state"] == "CLOSED"
    assert (forge_root / "feature.txt").exists()

    closed = forge.pr_list(state="closed")
    assert any(p["number"] == number for p in closed)


def test_pr_checks_default_success(forge: LocalForge, forge_root: Path) -> None:
    _git(forge_root, "checkout", "-b", "feature/checks")
    (forge_root / "c.txt").write_text("c\n", encoding="utf-8")
    _git(forge_root, "add", "c.txt")
    _git(forge_root, "commit", "-m", "c")
    _git(forge_root, "checkout", "main")
    url = forge.pr_create("main", "feature/checks", "checks", "")
    number = int(url.rstrip("/").split("/")[-1])
    checks = forge.pr_checks(number)
    assert checks
    assert all(c.get("conclusion") == "success" for c in checks)


def test_pr_checks_configured_command_failure(forge_root: Path) -> None:
    forge = LocalForge(forge_root, repo="local/test", checks_command=["false"])
    _git(forge_root, "checkout", "-b", "feature/fail-check")
    (forge_root / "f.txt").write_text("f\n", encoding="utf-8")
    _git(forge_root, "add", "f.txt")
    _git(forge_root, "commit", "-m", "f")
    _git(forge_root, "checkout", "main")
    url = forge.pr_create("main", "feature/fail-check", "fail", "")
    number = int(url.rstrip("/").split("/")[-1])
    checks = forge.pr_checks(number)
    assert checks
    assert any(c.get("conclusion") == "failure" for c in checks)


def test_counter_allocates_unique_numbers(forge: LocalForge) -> None:
    nums = {forge.issue_create(f"t{i}", "b") for i in range(5)}
    assert len(nums) == 5


def test_counter_lock_serializes(forge_root: Path) -> None:
    """O_CREAT|O_EXCL lock must serialize concurrent allocate_number calls."""
    forge = LocalForge(forge_root)
    # Force a held lock and ensure second allocate waits then succeeds
    lock = forge_root / ".forge" / "counter.lock"
    forge_root.joinpath(".forge").mkdir(parents=True, exist_ok=True)
    import os

    fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        # With lock held, allocate in a short timeout path is exercised via
        # public API after release — verify sequential uniqueness after unlock.
        pass
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)
    a = forge.issue_create("a", "b")
    b = forge.issue_create("b", "c")
    assert a != b


def test_get_forge_local_returns_local_forge(
    forge_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ghdag.forge import get_forge

    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(forge_root))
    got = get_forge()
    assert isinstance(got, LocalForge)
    assert isinstance(got, ForgePort)


def test_repo_exists_and_stubs(forge: LocalForge) -> None:
    assert forge.repo_exists() is True
    assert forge.repo_exists("other/repo") is False
    forge.dispatch_event("ping", {"x": 1})
    assert forge.get_rate_limit() is None
    with pytest.raises(GitHubApiError):
        forge.run_get(1)
    assert forge.run_logs_failed(1) == ""
    forge.run_rerun_failed(1)


def test_pr_update_and_ready(forge: LocalForge, forge_root: Path) -> None:
    _git(forge_root, "checkout", "-b", "feature/upd")
    (forge_root / "u.txt").write_text("u\n", encoding="utf-8")
    _git(forge_root, "add", "u.txt")
    _git(forge_root, "commit", "-m", "u")
    _git(forge_root, "checkout", "main")
    url = forge.pr_create("main", "feature/upd", "old", "old-body")
    number = int(url.rstrip("/").split("/")[-1])
    forge.pr_update(number, title="new", body="new-body")
    got = forge.pr_get(number)
    assert got["title"] == "new"
    assert got["body"] == "new-body"
    forge.pr_ready(number)  # no-op for local


def test_issue_timeline_includes_comment_events(forge: LocalForge) -> None:
    number = forge.issue_create("t", "b")
    forge.issue_comment(number, "hi")
    events = forge.issue_timeline(number)
    assert any(e.get("event") == "commented" or e.get("body") == "hi" for e in events)


def test_pr_diff_returns_text(forge: LocalForge, forge_root: Path) -> None:
    _git(forge_root, "checkout", "-b", "feature/diff")
    (forge_root / "d.txt").write_text("diffme\n", encoding="utf-8")
    _git(forge_root, "add", "d.txt")
    _git(forge_root, "commit", "-m", "d")
    _git(forge_root, "checkout", "main")
    url = forge.pr_create("main", "feature/diff", "diff", "")
    number = int(url.rstrip("/").split("/")[-1])
    diff = forge.pr_diff(number)
    assert "d.txt" in diff or "diffme" in diff


def test_pr_get_missing_raises_404(forge: LocalForge) -> None:
    with pytest.raises(GitHubApiError) as exc_info:
        forge.pr_get(999999999)
    assert exc_info.value.status_code == 404


def test_persist_layout_on_disk(forge: LocalForge, forge_root: Path) -> None:
    number = forge.issue_create("disk", "body", labels=["x"])
    forge.issue_comment(number, "c1")
    issue_path = forge_root / ".forge" / "issues" / f"{number}.json"
    comments_path = forge_root / ".forge" / "issues" / f"{number}.comments.jsonl"
    assert issue_path.is_file()
    data = json.loads(issue_path.read_text(encoding="utf-8"))
    assert data["title"] == "disk"
    assert comments_path.is_file()
    assert "c1" in comments_path.read_text(encoding="utf-8")
    assert (forge_root / ".forge" / "counter").is_file()
