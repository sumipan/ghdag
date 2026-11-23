"""Tests for GitHubClient ForgePort dispatcher-oriented methods."""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock

import pytest

from ghdag.core.exceptions import (
    AuthError,
    NetworkError,
    RateLimitError,
)
from ghdag.github_client import GitHubClient


def _make_client() -> GitHubClient:
    return GitHubClient(token="token", repo="owner/repo")


def _mock_urlopen(payload: object, status: int = 200, headers: dict | None = None):
    """Return a context-manager mock that simulates urllib.request.urlopen."""
    body = json.dumps(payload).encode()
    hdrs = headers or {}

    class _FakeResp:
        headers = hdrs

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


def test_issue_comment_calls_expected_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    captured: list[object] = []
    api_response = {"id": 99, "body": "text"}

    def fake_urlopen(req, timeout=120):
        captured.append(req)
        return _mock_urlopen(api_response)

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    result = client.issue_comment(42, "text")
    assert result == api_response
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
        client.issue_get(1)
    assert exc_info.value.status_code == 401


def test_raises_rate_limit_error_for_403_with_empty_remaining(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()

    def fake_urlopen(req, timeout=120):
        raise _mock_http_error(403, headers={"X-RateLimit-Remaining": "0"})

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RateLimitError) as exc_info:
        client.issue_get(1)
    assert exc_info.value.status_code == 403


def test_raises_network_error_for_url_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client()
    import urllib.error

    def fake_urlopen(req, timeout=120):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(NetworkError):
        client.issue_get(1)


