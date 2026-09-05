"""Unit tests for ghdag.github_cli."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from ghdag.github_cli import apply_jq, cli_main
from ghdag.github_client import GitHubClient, _resolve_token


def test_resolve_token_missing():
    from ghdag.exceptions import AuthError
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(AuthError, match="GITHUB_TOKEN is not set"):
            _resolve_token()


def test_resolve_token_from_env():
    with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}, clear=True):
        assert _resolve_token() == "tok"


def test_apply_jq_simple_field():
    assert apply_jq({"body": "hello"}, ".body") == "hello"


def test_apply_jq_labels_array():
    data = {"labels": [{"name": "a"}, {"name": "b"}]}
    assert apply_jq(data, "[.labels[].name]") == ["a", "b"]


def test_issue_get_fields_body(monkeypatch):
    client = GitHubClient(token="tok", repo="o/r")

    def fake_request(method, path, **kwargs):
        if path.endswith("/issues/1") and method == "GET":
            return {"body": "issue body", "labels": []}
        raise AssertionError(f"unexpected: {method} {path}")

    monkeypatch.setattr(client, "_request", fake_request)
    data = client.issue_get(1, fields=["body"])
    assert data == {"body": "issue body"}


def test_issue_update_labels(monkeypatch):
    client = GitHubClient(token="tok", repo="o/r")
    calls: list[tuple[str, str]] = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path))
        return {}

    monkeypatch.setattr(client, "_request", fake_request)
    client.issue_update(5, labels_remove=["old"], labels_add=["new"])
    methods = [c[0] for c in calls]
    assert "DELETE" in methods
    assert "POST" in methods


def test_cli_issue_view_json_jq(monkeypatch, capsys):
    class FakeClient:
        _token = "tok"

        def issue_get(self, number, fields=None):
            assert number == 1360
            assert fields == ["body"]
            return {"body": "design text"}

    monkeypatch.setattr(
        "ghdag.github_cli.GitHubClient",
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
    """issue edit --title 単体で issue_update に title が渡り、PATCH に含まれる。"""
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
        "ghdag.github_cli.GitHubClient",
        lambda *a, **k: client,
    )
    with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}, clear=False):
        rc = cli_main(["issue", "edit", "5", "--title", "新タイトル"])
    assert rc == 0
    assert update_calls == [
        {
            "number": 5,
            "title": "新タイトル",
            "body": None,
            "labels_add": None,
            "labels_remove": None,
        }
    ]
    assert patch_calls == [{"title": "新タイトル"}]


def test_cli_issue_edit_title_and_body_file(monkeypatch, tmp_path):
    """--title と --body-file を同時に渡すと両方が issue_update に渡る。"""
    body_file = tmp_path / "body.md"
    body_file.write_text("本文内容", encoding="utf-8")
    captured: dict[str, object] = {}

    class FakeClient:
        _token = "tok"

        def issue_update(self, number, **kwargs):
            captured["number"] = number
            captured["kwargs"] = kwargs

    monkeypatch.setattr(
        "ghdag.github_cli.GitHubClient",
        lambda *a, **k: FakeClient(),
    )
    with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}, clear=False):
        rc = cli_main(
            ["issue", "edit", "5", "--title", "T", "--body-file", str(body_file)]
        )
    assert rc == 0
    assert captured["number"] == 5
    assert captured["kwargs"]["title"] == "T"
    assert captured["kwargs"]["body"] == "本文内容"


def test_cli_issue_edit_unknown_arg_warning_excludes_title(monkeypatch, capsys):
    """未知引数 warning は --foobar のみ。--title は unknown に含まれない。"""

    class FakeClient:
        _token = "tok"

        def issue_update(self, number, **kwargs):
            pass

    monkeypatch.setattr(
        "ghdag.github_cli.GitHubClient",
        lambda *a, **k: FakeClient(),
    )
    with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}, clear=False):
        rc = cli_main(["issue", "edit", "5", "--foobar", "--title", "T"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "warning: unknown args ignored:" in err
    assert "--foobar" in err
    assert "--title" not in err
    assert "'T'" not in err


def test_cli_pr_edit_body(monkeypatch):
    """pr edit <url> --body が pr_update(number, body=...) を呼ぶ。URL からも番号を抽出する。"""
    captured: dict[str, object] = {}

    class FakeClient:
        _token = "tok"

        def pr_update(self, number, **kwargs):
            captured["number"] = number
            captured["kwargs"] = kwargs

    monkeypatch.setattr(
        "ghdag.github_cli.GitHubClient",
        lambda *a, **k: FakeClient(),
    )
    with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}, clear=False):
        rc = cli_main(
            ["pr", "edit", "https://github.com/o/r/pull/2884", "--body", "Refs #1"]
        )
    assert rc == 0
    assert captured["number"] == 2884
    assert captured["kwargs"] == {"title": None, "body": "Refs #1"}


def test_cli_pr_edit_number_and_title(monkeypatch):
    """素の番号と --title の組み合わせ。"""
    captured: dict[str, object] = {}

    class FakeClient:
        _token = "tok"

        def pr_update(self, number, **kwargs):
            captured["number"] = number
            captured["kwargs"] = kwargs

    monkeypatch.setattr(
        "ghdag.github_cli.GitHubClient",
        lambda *a, **k: FakeClient(),
    )
    with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "tok"}, clear=False):
        rc = cli_main(["pr", "edit", "42", "--title", "新タイトル"])
    assert rc == 0
    assert captured["number"] == 42
    assert captured["kwargs"]["title"] == "新タイトル"
    assert captured["kwargs"]["body"] is None


def test_pr_update_patches_pulls_endpoint(monkeypatch):
    """GitHubClient.pr_update は issues ではなく pulls エンドポイントを PATCH する。"""
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
