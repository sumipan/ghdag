"""Unit tests for ghdag.github_client (urllib-based GitHubClient)."""

from __future__ import annotations

import io
import json
import os
from unittest import mock

import pytest
import urllib.error

from ghdag.github_client import (
    API_BASE,
    DEFAULT_REPO,
    GRAPHQL_URL,
    GitHubClient,
    _resolve_repo,
    _resolve_token,
)


def test_module_exports_importable() -> None:
    from ghdag.github_client import GitHubClient as GC  # noqa: F401

    assert API_BASE == "https://api.github.com"
    assert GRAPHQL_URL == "https://api.github.com/graphql"
    assert DEFAULT_REPO == "sumipan/nexus"


def test_client_repo_property() -> None:
    client = GitHubClient(token="t", repo="o/r")
    assert client.repo == "o/r"


def test_user_agent_header() -> None:
    client = GitHubClient(token="t", repo="o/r")
    assert client._headers()["User-Agent"] == "ghdag-github-client"


def test_resolve_token_missing() -> None:
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="GITHUB_TOKEN is not set"):
            _resolve_token()
        with pytest.raises(ValueError, match="GITHUB_TOKEN is not set"):
            GitHubClient()


def test_resolve_repo_invalid_format() -> None:
    with pytest.raises(ValueError, match="Invalid repo format"):
        _resolve_repo("not-a-repo")


def test_issue_get_fields_body(monkeypatch: pytest.MonkeyPatch) -> None:
    client = GitHubClient(token="tok", repo="o/r")

    def fake_request(method: str, path: str, **kwargs: object) -> dict:
        if method == "GET" and path.endswith("/issues/1"):
            return {"body": "issue body", "labels": []}
        raise AssertionError(f"unexpected: {method} {path}")

    monkeypatch.setattr(client, "_request", fake_request)
    assert client.issue_get(1, fields=["body"]) == {"body": "issue body"}


def test_issue_get_fields_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    client = GitHubClient(token="tok", repo="o/r")

    def fake_request(method: str, path: str, **kwargs: object) -> list | dict:
        if method == "GET" and path.endswith("/issues/1") and "comments" not in path:
            return {"number": 1}
        if method == "GET" and path.endswith("/comments"):
            return [
                {
                    "body": "hi",
                    "user": {"login": "alice"},
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
        raise AssertionError(f"unexpected: {method} {path}")

    monkeypatch.setattr(client, "_request", fake_request)
    data = client.issue_get(1, fields=["comments"])
    assert data == {
        "comments": [
            {
                "body": "hi",
                "author": {"login": "alice"},
                "createdAt": "2026-01-01T00:00:00Z",
            }
        ]
    }


def test_issue_update_body_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    client = GitHubClient(token="tok", repo="o/r")
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method: str, path: str, **kwargs: object) -> dict:
        body = kwargs.get("body")
        calls.append((method, path, body if isinstance(body, dict) else body))
        return {}

    monkeypatch.setattr(client, "_request", fake_request)
    client.issue_update(
        5,
        body="new body",
        labels_remove=["old"],
        labels_add=["new"],
    )
    methods = [c[0] for c in calls]
    paths = [c[1] for c in calls]
    assert "PATCH" in methods
    assert any("issues/5" in p and "labels" not in p for p in paths)
    assert any(c[0] == "DELETE" and "labels/old" in c[1] for c in calls)
    assert any(c[0] == "POST" and c[1].endswith("/issues/5/labels") for c in calls)
    patch_call = next(c for c in calls if c[0] == "PATCH")
    assert patch_call[2] == {"body": "new body"}


def test_pr_create_builds_owner_head_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    client = GitHubClient(token="tok", repo="o/r")
    captured: dict[str, object] = {}

    def fake_request(method: str, path: str, **kwargs: object) -> dict:
        captured["method"] = method
        captured["path"] = path
        captured["body"] = kwargs.get("body")
        return {"html_url": "https://github.com/o/r/pull/1"}

    monkeypatch.setattr(client, "_request", fake_request)
    url = client.pr_create("main", "feature", "title", "body")
    assert url == "https://github.com/o/r/pull/1"
    assert captured["method"] == "POST"
    assert captured["body"] == {
        "title": "title",
        "body": "body",
        "head": "o:feature",
        "base": "main",
    }


def test_pr_ready_sends_graphql_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    client = GitHubClient(token="tok", repo="o/r")

    def fake_request(method: str, path: str, **kwargs: object) -> dict:
        if method == "GET":
            return {"node_id": "PR_node_123"}
        raise AssertionError(f"unexpected REST: {method} {path}")

    monkeypatch.setattr(client, "_request", fake_request)

    class FakeResp:
        def read(self) -> bytes:
            return json.dumps({"data": {"markPullRequestReadyForReview": {}}}).encode()

        def __enter__(self) -> FakeResp:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    captured_req: list[object] = []

    def fake_urlopen(req: object, timeout: int = 60) -> FakeResp:
        captured_req.append(req)
        return FakeResp()

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    client.pr_ready(7)
    assert len(captured_req) == 1
    req = captured_req[0]
    assert req.full_url == GRAPHQL_URL  # type: ignore[attr-defined]
    payload = json.loads(req.data.decode())  # type: ignore[attr-defined]
    assert "markPullRequestReadyForReview" in payload["query"]
    assert payload["variables"] == {"id": "PR_node_123"}


def test_api_request_expands_owner_repo_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = GitHubClient(token="tok", repo="o/r")
    seen: list[str] = []

    def fake_request(method: str, path: str, **kwargs: object) -> dict:
        seen.append(path)
        return {"ok": True}

    monkeypatch.setattr(client, "_request", fake_request)
    client.api_request("repos/:owner/:repo/pulls/1")
    assert seen == ["/repos/o/r/pulls/1"]


def test_http_error_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = GitHubClient(token="tok", repo="o/r")

    def fake_urlopen(req: object, timeout: int = 120) -> None:
        body = json.dumps({"message": "Not Found"}).encode()
        raise urllib.error.HTTPError(
            url="https://api.github.com/x",
            code=404,
            msg="Not Found",
            hdrs=mock.MagicMock(),
            fp=io.BytesIO(body),
        )

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="404.*Not Found"):
        client._request("GET", "/repos/o/r/issues/1")
