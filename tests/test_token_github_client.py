"""Tests for GitHubClient Protocol-compatible methods (GitHubIssuePort interface)."""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from ghdag.exceptions import (
    AuthError,
    NetworkError,
    PermissionDeniedError,
    RateLimitError,
)
from ghdag.github_client import GitHubClient


def _make_client() -> GitHubClient:
    return GitHubClient(token="token", repo="owner/repo")


def _mock_urlopen(payload: object, status: int = 200, headers: dict | None = None):
    """Return a context-manager mock that simulates urllib.request.urlopen."""
    body = json.dumps(payload).encode()

    class _FakeResp:
        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    return _FakeResp()


def _mock_http_error(code: int, message: str = "error", headers: dict | None = None):
    hdrs = MagicMock()
    hdrs.get = lambda k, d=None: (headers or {}).get(k, d)
    body = json.dumps({"message": message}).encode()
    return urllib.error.HTTPError(
        url="https://api.github.com/x",
        code=code,
        msg=message,
        hdrs=hdrs,
        fp=io.BytesIO(body),
    )


def test_get_issue_calls_expected_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    monkeypatch.setattr(
        "ghdag.github_client.urllib.request.urlopen",
        lambda req, timeout=120: _mock_urlopen({"number": 42}),
    )
    result = client.get_issue(42)
    assert result["number"] == 42


def test_list_issues_calls_expected_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    captured: list[str] = []

    def fake_urlopen(req, timeout=120):
        captured.append(req.full_url)
        return _mock_urlopen([{"number": 1}])

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    result = client.list_issues("bug", "open")
    assert result == [{"number": 1}]
    assert "labels=bug" in captured[0]
    assert "state=open" in captured[0]


def test_add_comment_calls_expected_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    captured: list[object] = []

    def fake_urlopen(req, timeout=120):
        captured.append(req)
        return _mock_urlopen({})

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    client.add_comment(42, "text")
    assert len(captured) == 1
    req = captured[0]
    assert "/issues/42/comments" in req.full_url
    assert req.get_method() == "POST"
    body = json.loads(req.data.decode())
    assert body == {"body": "text"}


def test_update_label_uses_delete_then_post(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    calls: list[tuple[str, str]] = []

    def fake_urlopen(req, timeout=120):
        calls.append((req.get_method(), req.full_url))
        return _mock_urlopen({})

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    client.update_label(42, "old", "new")
    assert len(calls) == 2
    assert calls[0][0] == "DELETE"
    assert "labels/old" in calls[0][1]
    assert calls[1][0] == "POST"
    assert calls[1][1].endswith("/issues/42/labels")


def test_remove_label_calls_delete_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    captured: list[object] = []

    def fake_urlopen(req, timeout=120):
        captured.append(req)
        return _mock_urlopen({})

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    client.remove_label(42, "old")
    assert len(captured) == 1
    req = captured[0]
    assert req.get_method() == "DELETE"
    assert "labels/old" in req.full_url


def test_dispatch_event_calls_expected_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    captured: list[object] = []

    def fake_urlopen(req, timeout=120):
        captured.append(req)
        return _mock_urlopen({})

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    client.dispatch_event("trigger", {"key": "val"})
    assert len(captured) == 1
    req = captured[0]
    assert "/dispatches" in req.full_url
    body = json.loads(req.data.decode())
    assert body == {"event_type": "trigger", "client_payload": {"key": "val"}}


def test_get_rate_limit_calls_expected_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()

    def fake_urlopen(req, timeout=120):
        assert "/rate_limit" in req.full_url
        return _mock_urlopen({"rate": {}})

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    result = client.get_rate_limit()
    assert result == {"rate": {}}


def test_raises_auth_error_for_401(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()

    def fake_urlopen(req, timeout=120):
        raise _mock_http_error(401)

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(AuthError) as exc_info:
        client.get_issue(1)
    assert exc_info.value.status_code == 401


def test_raises_rate_limit_error_for_403_with_empty_remaining(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()

    def fake_urlopen(req, timeout=120):
        raise _mock_http_error(403, headers={"X-RateLimit-Remaining": "0"})

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RateLimitError) as exc_info:
        client.get_issue(1)
    assert exc_info.value.status_code == 403


def test_raises_permission_denied_for_403_non_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()

    def fake_urlopen(req, timeout=120):
        raise _mock_http_error(403, headers={"X-RateLimit-Remaining": "10"})

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(PermissionDeniedError) as exc_info:
        client.get_issue(1)
    assert exc_info.value.status_code == 403


def test_raises_network_error_for_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    import urllib.error

    def fake_urlopen(req, timeout=120):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(NetworkError):
        client.get_issue(1)


def test_get_issue_comments_normalizes_api_response(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    raw = [
        {
            "user": {"login": "alice"},
            "created_at": "2026-01-01T00:00:00Z",
            "body": "hello",
        }
    ]

    def fake_urlopen(req, timeout=120):
        assert "/comments" in req.full_url
        return _mock_urlopen(raw)

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    result = client.get_issue_comments(42)
    assert result == [
        {
            "author": "alice",
            "created_at": "2026-01-01T00:00:00Z",
            "body": "hello",
        }
    ]


def test_get_issue_comments_null_user_yields_empty_author(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    raw = [{"user": None, "created_at": "2026-01-01T00:00:00Z", "body": "ghost"}]

    monkeypatch.setattr(
        "ghdag.github_client.urllib.request.urlopen",
        lambda req, timeout=120: _mock_urlopen(raw),
    )
    result = client.get_issue_comments(1)
    assert result == [{"author": "", "created_at": "2026-01-01T00:00:00Z", "body": "ghost"}]


def test_get_issue_comments_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()

    monkeypatch.setattr(
        "ghdag.github_client.urllib.request.urlopen",
        lambda req, timeout=120: _mock_urlopen([]),
    )
    result = client.get_issue_comments(99)
    assert result == []
