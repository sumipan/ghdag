"""Unit tests for ghdag.github_cli."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from ghdag.forge.local import LocalForge
from ghdag.github_cli import apply_jq, cli_main
from ghdag.github_client import GitHubClient, _resolve_token


def test_resolve_token_from_env():
    with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}, clear=True):
        assert _resolve_token() == "tok"


def test_apply_jq_simple_field():
    assert apply_jq({"body": "hello"}, ".body") == "hello"


def test_apply_jq_labels_array():
    data = {"labels": [{"name": "a"}, {"name": "b"}]}
    assert apply_jq(data, "[.labels[].name]") == ["a", "b"]


def test_cli_issue_view_json_jq(monkeypatch, capsys):
    class FakeClient:
        _token = "tok"

        def issue_get(self, number, fields=None):
            assert number == 1360
            assert fields == ["body"]
            return {"body": "design text"}

    monkeypatch.setattr(
        "ghdag.github_cli.get_forge",
        lambda *a, **k: FakeClient(),
    )
    rc = cli_main(["issue", "view", "1360", "--json", "body", "--jq", ".body"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "design text"


def test_cli_missing_token(capsys):
    with mock.patch.dict(os.environ, {}, clear=True):
        rc = cli_main(["issue", "view", "1"])
    assert rc == 1
    assert "GITHUB_TOKEN is not set" in capsys.readouterr().err


def test_cli_issue_edit_title_only(monkeypatch):
    """issue edit --title alone passes title to issue_update and into PATCH."""
    update_calls: list[dict[str, object]] = []
    patch_calls: list[dict] = []

    class TrackingClient(GitHubClient):
        def issue_update(self, number, **kwargs):  # type: ignore[no-untyped-def]
            update_calls.append({"number": number, **kwargs})
            return super().issue_update(number, **kwargs)

    client = TrackingClient(token="tok", repo="o/r")

    def fake_request(method, path, **kwargs):
        if method == "PATCH":
            body = kwargs.get("body")
            assert isinstance(body, dict)
            patch_calls.append(body)
        return {}

    monkeypatch.setattr(client, "_request", fake_request)
    monkeypatch.setattr(
        "ghdag.github_cli.get_forge",
        lambda *a, **k: client,
    )
    with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}, clear=False):
        rc = cli_main(["issue", "edit", "5", "--title", "New title"])
    assert rc == 0
    assert update_calls == [
        {
            "number": 5,
            "title": "New title",
            "body": None,
            "labels_add": None,
            "labels_remove": None,
        }
    ]
    assert patch_calls == [{"title": "New title"}]


def test_cli_issue_edit_title_and_body_file(monkeypatch, tmp_path):
    """Passing --title and --body-file together forwards both to issue_update."""
    body_file = tmp_path / "body.md"
    body_file.write_text("Body content", encoding="utf-8")
    captured: dict[str, object] = {}

    class FakeClient:
        _token = "tok"

        def issue_update(self, number, **kwargs):
            captured["number"] = number
            captured["kwargs"] = kwargs

    monkeypatch.setattr(
        "ghdag.github_cli.get_forge",
        lambda *a, **k: FakeClient(),
    )
    with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}, clear=False):
        rc = cli_main(
            ["issue", "edit", "5", "--title", "T", "--body-file", str(body_file)]
        )
    assert rc == 0
    assert captured["number"] == 5
    assert captured["kwargs"]["title"] == "T"
    assert captured["kwargs"]["body"] == "Body content"


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.fixture
def local_forge_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "forge_data"
    forge = LocalForge(root, repo="local/test")
    forge.issue_create("Seed issue", "issue body")
    forge.pr_create("main", "feature", "Seed PR", "pr body")
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(root))
    return root


@pytest.mark.parametrize(
    "argv_prefix",
    [
        pytest.param(["issue", "view", "1"], id="issue-view"),
        pytest.param(["issue", "create", "--title", "T"], id="issue-create"),
        pytest.param(["issue", "edit", "1"], id="issue-edit"),
        pytest.param(["issue", "comment", "1", "--body", "c"], id="issue-comment"),
        pytest.param(["issue", "close", "1"], id="issue-close"),
        pytest.param(["pr", "list"], id="pr-list"),
        pytest.param(["pr", "view", "1"], id="pr-view"),
        pytest.param(
            ["pr", "create", "--base", "main", "--head", "h", "--title", "T"],
            id="pr-create",
        ),
        pytest.param(["pr", "edit", "1"], id="pr-edit"),
        pytest.param(["pr", "merge", "1"], id="pr-merge"),
        pytest.param(["pr", "diff", "1"], id="pr-diff"),
        pytest.param(["pr", "checks", "1"], id="pr-checks"),
        pytest.param(["pr", "ready", "1"], id="pr-ready"),
        pytest.param(["run", "view", "1"], id="run-view"),
        pytest.param(["run", "rerun", "1"], id="run-rerun"),
        pytest.param(["repo", "view"], id="repo-view"),
        pytest.param(["api", "repos/local/test"], id="api"),
    ],
)
def test_unknown_arg_fails_without_forge_call(
    local_forge_cli: Path, argv_prefix: list[str]
) -> None:
    """Refs sumipan/nexus#6698: unknown flags fail before forge is invoked."""
    before = _snapshot_tree(local_forge_cli)
    rc = cli_main([*argv_prefix, "--no-such-flag"])
    after = _snapshot_tree(local_forge_cli)
    assert rc == 2
    assert before == after


def test_unknown_subcommand_lists_available_subcommands(
    local_forge_cli: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refs sumipan/nexus#6698: invalid issue subcommands list valid choices."""
    rc = cli_main(["issue", "list"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "error[CLI_USAGE]:" in err
    for name in ("create", "edit", "view", "comment", "close"):
        assert name in err


def test_cli_issue_close_reason_not_planned(
    local_forge_cli: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Issue #7952 AC8: issue close --reason not_planned persists via LocalForge."""
    rc = cli_main(["issue", "close", "1", "--reason", "not_planned"])
    assert rc == 0
    forge = LocalForge(local_forge_cli, repo="local/test")
    assert forge.issue_get(1, fields=["state", "state_reason"]) == {
        "state": "CLOSED",
        "state_reason": "not_planned",
    }


def test_pr_create_body_file(
    local_forge_cli: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refs sumipan/nexus#6698: pr create reads --body-file into the PR body."""
    body_file = tmp_path / "body.md"
    body_file.write_text("Body from file\n", encoding="utf-8")
    rc = cli_main(
        [
            "pr",
            "create",
            "--base",
            "main",
            "--head",
            "feature",
            "--title",
            "T",
            "--body-file",
            str(body_file),
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    number = int(captured.out.strip().rstrip("/").split("/")[-1])
    forge = LocalForge(local_forge_cli, repo="local/test")
    assert forge.pr_get(number)["body"] == "Body from file\n"


def test_cli_pr_edit_number_and_title(monkeypatch):
    """Bare number combined with --title."""
    captured: dict[str, object] = {}

    class FakeClient:
        _token = "tok"

        def pr_update(self, number, **kwargs):
            captured["number"] = number
            captured["kwargs"] = kwargs

    monkeypatch.setattr(
        "ghdag.github_cli.get_forge",
        lambda *a, **k: FakeClient(),
    )
    with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}, clear=False):
        rc = cli_main(["pr", "edit", "42", "--title", "New title"])
    assert rc == 0
    assert captured["number"] == 42
    assert captured["kwargs"]["title"] == "New title"
    assert captured["kwargs"]["body"] is None


def test_pr_update_patches_pulls_endpoint(monkeypatch):
    """GitHubClient.pr_update PATCHes pulls, not issues."""
    client = GitHubClient(token="tok", repo="o/r")
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get("body")))
        return {}

    monkeypatch.setattr(client, "_request", fake_request)
    client.pr_update(7, body="new body")
    assert calls == [("PATCH", "/repos/o/r/pulls/7", {"body": "new body"})]


def test_pr_diff_raw(monkeypatch):
    client = GitHubClient(token="tok", repo="o/r")

    def fake_request(method, path, **kwargs):
        assert kwargs.get("raw") is True
        return "diff content"

    monkeypatch.setattr(client, "_request", fake_request)
    assert client.pr_diff(9) == "diff content"
