from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
import requests

from ghdag.workflow.github import (
    AuthError,
    NetworkError,
    PermissionDeniedError,
    RateLimitError,
    TokenGitHubClient,
)


def _response(
    status_code: int = 200,
    payload: object | None = None,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    res = MagicMock()
    res.status_code = status_code
    res.headers = headers or {}
    res.json.return_value = payload if payload is not None else {}
    if status_code >= 400:
        res.raise_for_status.side_effect = requests.HTTPError(f"http {status_code}")
    else:
        res.raise_for_status.return_value = None
    return res


def test_constructor_sets_bearer_header() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    assert client._session.headers["Authorization"] == "Bearer token"
    assert client._session.headers["Accept"] == "application/vnd.github+json"


def test_get_issue_calls_expected_endpoint() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    client._session.request = MagicMock(return_value=_response(payload={"number": 42}))
    result = client.get_issue(42)
    client._session.request.assert_called_once_with(
        "GET",
        "https://api.github.com/repos/owner/repo/issues/42",
    )
    assert result == {"number": 42}


def test_list_issues_calls_expected_endpoint() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    client._session.request = MagicMock(return_value=_response(payload=[{"number": 1}]))
    result = client.list_issues("bug", "open")
    client._session.request.assert_called_once_with(
        "GET",
        "https://api.github.com/repos/owner/repo/issues",
        params={"labels": "bug", "state": "open", "per_page": 100},
    )
    assert result == [{"number": 1}]


def test_add_comment_calls_expected_endpoint() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    client._session.request = MagicMock(return_value=_response())
    client.add_comment(42, "text")
    client._session.request.assert_called_once_with(
        "POST",
        "https://api.github.com/repos/owner/repo/issues/42/comments",
        json={"body": "text"},
    )


def test_update_label_uses_delete_then_post() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    client._session.request = MagicMock(side_effect=[_response(), _response()])
    client.update_label(42, "old", "new")
    assert client._session.request.call_count == 2
    first = client._session.request.call_args_list[0]
    second = client._session.request.call_args_list[1]
    assert first.args == ("DELETE", "https://api.github.com/repos/owner/repo/issues/42/labels/old")
    assert second.args == ("POST", "https://api.github.com/repos/owner/repo/issues/42/labels")
    assert second.kwargs == {"json": ["new"]}


def test_remove_label_calls_delete_endpoint() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    client._session.request = MagicMock(return_value=_response())
    client.remove_label(42, "old")
    client._session.request.assert_called_once_with(
        "DELETE",
        "https://api.github.com/repos/owner/repo/issues/42/labels/old",
    )


def test_dispatch_event_calls_expected_endpoint() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    client._session.request = MagicMock(return_value=_response())
    client.dispatch_event("trigger", {"key": "val"})
    client._session.request.assert_called_once_with(
        "POST",
        "https://api.github.com/repos/owner/repo/dispatches",
        json={"event_type": "trigger", "client_payload": {"key": "val"}},
    )


def test_get_rate_limit_calls_expected_endpoint() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    client._session.request = MagicMock(return_value=_response(payload={"rate": {}}))
    result = client.get_rate_limit()
    client._session.request.assert_called_once_with(
        "GET",
        "https://api.github.com/rate_limit",
    )
    assert result == {"rate": {}}


def test_request_logs_rate_limit_headers(caplog: pytest.LogCaptureFixture) -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    client._session.request = MagicMock(
        return_value=_response(headers={"X-RateLimit-Remaining": "123", "X-RateLimit-Reset": "999"})
    )
    with caplog.at_level(logging.DEBUG, logger="ghdag.workflow.github"):
        client.get_issue(1)
    assert "remaining=123" in caplog.text
    assert "reset=999" in caplog.text


def test_raises_auth_error_for_401() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    client._session.request = MagicMock(return_value=_response(status_code=401))
    with pytest.raises(AuthError) as exc_info:
        client.get_issue(1)
    assert exc_info.value.status_code == 401


def test_raises_rate_limit_error_for_403_with_empty_remaining() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    client._session.request = MagicMock(
        return_value=_response(status_code=403, headers={"X-RateLimit-Remaining": "0"})
    )
    with pytest.raises(RateLimitError) as exc_info:
        client.get_issue(1)
    assert exc_info.value.status_code == 403


def test_raises_permission_denied_for_403_non_rate_limit() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    client._session.request = MagicMock(
        return_value=_response(status_code=403, headers={"X-RateLimit-Remaining": "10"})
    )
    with pytest.raises(PermissionDeniedError) as exc_info:
        client.get_issue(1)
    assert exc_info.value.status_code == 403


def test_raises_network_error_for_connection_error() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    client._session.request = MagicMock(side_effect=requests.ConnectionError("down"))
    with pytest.raises(NetworkError):
        client.get_issue(1)


def test_raises_network_error_for_timeout() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    client._session.request = MagicMock(side_effect=requests.Timeout("timeout"))
    with pytest.raises(NetworkError):
        client.get_issue(1)


def test_get_issue_comments_normalizes_api_response() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    raw = [
        {
            "user": {"login": "alice"},
            "created_at": "2026-01-01T00:00:00Z",
            "body": "hello",
        }
    ]
    client._session.request = MagicMock(return_value=_response(payload=raw))
    result = client.get_issue_comments(42)
    client._session.request.assert_called_once_with(
        "GET",
        "https://api.github.com/repos/owner/repo/issues/42/comments",
        params={"per_page": 100},
    )
    assert result == [
        {
            "author": "alice",
            "created_at": "2026-01-01T00:00:00Z",
            "body": "hello",
        }
    ]


def test_get_issue_comments_null_user_yields_empty_author() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    raw = [{"user": None, "created_at": "2026-01-01T00:00:00Z", "body": "ghost"}]
    client._session.request = MagicMock(return_value=_response(payload=raw))
    result = client.get_issue_comments(1)
    assert result == [
        {"author": "", "created_at": "2026-01-01T00:00:00Z", "body": "ghost"}
    ]


def test_get_issue_comments_empty_list() -> None:
    client = TokenGitHubClient("token", "owner", "repo")
    client._session.request = MagicMock(return_value=_response(payload=[]))
    result = client.get_issue_comments(99)
    assert result == []
