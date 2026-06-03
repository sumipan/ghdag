"""Unit tests for ghdag.github_cli."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from ghdag.github_cli import apply_jq, cli_main
from ghdag.github_client import GitHubClient, _resolve_token


def test_resolve_token_missing():
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="GITHUB_TOKEN is not set"):
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


def test_pr_diff_raw(monkeypatch):
    client = GitHubClient(token="tok", repo="o/r")

    def fake_request(method, path, **kwargs):
        assert kwargs.get("raw") is True
        return "diff content"

    monkeypatch.setattr(client, "_request", fake_request)
    assert client.pr_diff(9) == "diff content"
