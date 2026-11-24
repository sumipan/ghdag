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



def test_issue_close_not_planned_sets_state_reason(forge: LocalForge) -> None:
    """Issue #7952 AC1: close with not_planned persists state_reason."""
    number = forge.issue_create("x", "b")
    forge.issue_close(number, reason="not_planned")
    got = forge.issue_get(number, fields=["state", "state_reason"])
    assert got == {"state": "CLOSED", "state_reason": "not_planned"}


def test_issue_close_default_completed_and_reopen_clears_reason(
    forge: LocalForge,
) -> None:
    """Issue #7952 AC2: default completed; reopen clears state_reason."""
    number = forge.issue_create("x", "b")
    forge.issue_close(number)
    assert (
        forge.issue_get(number, fields=["state_reason"])["state_reason"] == "completed"
    )
    forge.reopen_issue(number)
    assert forge.issue_get(number, fields=["state_reason"])["state_reason"] is None


def test_issue_get_state_reason_legacy_closed_is_completed(
    forge: LocalForge, forge_root: Path
) -> None:
    """Issue #7952 AC3: legacy closed issues without state_reason read as completed."""
    number = forge.issue_create("legacy", "b")
    path = forge_root / ".forge" / "issues" / f"{number}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["state"] = "closed"
    data.pop("state_reason", None)
    path.write_text(json.dumps(data), encoding="utf-8")
    assert (
        forge.issue_get(number, fields=["state_reason"])["state_reason"] == "completed"
    )


def test_issue_close_invalid_reason_raises_without_change(forge: LocalForge) -> None:
    """Issue #7952 AC4: invalid reason leaves issue open."""
    number = forge.issue_create("x", "b")
    with pytest.raises(ValueError):
        forge.issue_close(number, reason="other")
    assert forge.issue_get(number, fields=["state"])["state"] == "OPEN"


def test_graphql_not_supported_and_protocol_conformance(tmp_path: Path) -> None:
    from ghdag.github_client import GitHubClient

    forge = LocalForge(tmp_path / "forge")
    with pytest.raises(NotImplementedError):
        forge.graphql("query { x }")
    assert isinstance(forge, ForgePort)
    assert isinstance(GitHubClient(token="t", repo="o/r"), ForgePort)


def test_list_all_issues_filters_by_state(forge: LocalForge) -> None:
    """Issue #6093 AC2: list_all_issues filters open / closed / all without label filter."""
    o1 = forge.issue_create("open-a", "b", labels=["a"])
    o2 = forge.issue_create("open-b", "b", labels=["b"])
    closed = forge.issue_create("closed-one", "b")
    forge.issue_close(closed)

    open_nums = {i["number"] for i in forge.list_all_issues("open")}
    assert open_nums == {o1, o2}
    closed_nums = {i["number"] for i in forge.list_all_issues("closed")}
    assert closed_nums == {closed}
    all_nums = {i["number"] for i in forge.list_all_issues("all")}
    assert all_nums == {o1, o2, closed}




def test_issue_comment_appends_comment(forge: LocalForge) -> None:
    number = forge.issue_create("c", "b")
    result = forge.issue_comment(number, "hello")
    assert result["body"] == "hello"
    comments = forge.get_issue_comments(number)
    assert comments[-1]["body"] == "hello"


def test_issue_get_returns_id_equal_to_number(forge: LocalForge) -> None:
    """Issue #6093 AC6: issue id mirrors issue number."""
    number = forge.issue_create("id-test", "b")
    assert forge.issue_get(number)["id"] == number
    assert forge.issue_get(number, fields=["id", "number"]) == {
        "id": number,
        "number": number,
    }


def test_sub_issue_add_list_summary(forge: LocalForge) -> None:
    """Issue #6093 AC7: sub-issue link, list, and summary."""
    parent = forge.issue_create("parent", "b")
    c1 = forge.issue_create("c1", "b")
    c2 = forge.issue_create("c2", "b")
    assert forge.sub_issues_summary(parent) == {
        "total": 0,
        "completed": 0,
        "percent_completed": 0,
    }
    forge.add_sub_issue(parent, c1)
    forge.add_sub_issue(parent, c2)
    forge.issue_close(c2)
    listed = [i["number"] for i in forge.list_sub_issues(parent)]
    assert listed == [c1, c2]
    assert forge.sub_issues_summary(parent) == {
        "total": 2,
        "completed": 1,
        "percent_completed": 50,
    }


def test_add_sub_issue_duplicate_returns_none(forge: LocalForge) -> None:
    """Issue #6093 AC8: duplicate add_sub_issue is idempotent."""
    parent = forge.issue_create("p", "b")
    child = forge.issue_create("c", "b")
    first = forge.add_sub_issue(parent, child)
    assert first is not None
    assert first["number"] == parent
    assert forge.add_sub_issue(parent, child) is None
    assert len(forge.list_sub_issues(parent)) == 1


def test_remove_sub_issue_unlinks(forge: LocalForge) -> None:
    """Issue #6093 AC9: remove_sub_issue unlinks or no-ops."""
    parent = forge.issue_create("p", "b")
    child = forge.issue_create("c", "b")
    forge.add_sub_issue(parent, child)
    removed = forge.remove_sub_issue(parent, child)
    assert removed is not None
    assert removed["number"] == parent
    assert forge.list_sub_issues(parent) == []

    other_parent = forge.issue_create("p2", "b")
    assert forge.remove_sub_issue(other_parent, child) is None


def test_add_sub_issue_missing_child_raises_404(
    forge: LocalForge, forge_root: Path
) -> None:
    """Issue #6093 AC10: missing child yields 404 and leaves parent unchanged."""
    parent = forge.issue_create("p", "b")
    with pytest.raises(GitHubApiError) as exc_info:
        forge.add_sub_issue(parent, 9999)
    assert exc_info.value.status_code == 404
    parent_data = json.loads(
        (forge_root / ".forge" / "issues" / f"{parent}.json").read_text(
            encoding="utf-8"
        )
    )
    assert "sub_issues" not in parent_data
