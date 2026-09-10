"""Contract tests for GitHubClient sub-issue REST methods (#3125 / #3076).

Fixtures mirror real API probes (2026-09-10, CLAUDE.md §10) against
sumipan/nexus parent #3017 / child #3033 (id=5407000506):

| operation | result |
|---|---|
| POST sub_issues | 201, parent JSON with sub_issues_summary |
| duplicate POST | 422 ``Issue may not contain duplicate sub-issues...`` |
| number as child_id | 403 ``Resource not accessible by personal access token`` |
| GET issue | sub_issues_summary ``{total:3, completed:3, percent_completed:100}`` |
| GET sub_issues | children include number/state/id |
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any
from unittest import mock

import pytest

from ghdag.core.ports.github import GitHubIssuePort
from ghdag.exceptions import PermissionDeniedError
from ghdag.github_client import GitHubClient

# --- Real probe constants (2026-09-10) ---
_PARENT = 3017
_CHILD_NUMBER = 3033
_CHILD_ID = 5407000506
_REAL_SUMMARY = {"total": 3, "completed": 3, "percent_completed": 100}
_EMPTY_SUMMARY = {"total": 0, "completed": 0, "percent_completed": 0}
_DUP_MSG = "Issue may not contain duplicate sub-issues and Sub issue may only have one parent"
_FORBIDDEN_MSG = "Resource not accessible by personal access token"

# Minimal parent JSON returned on 201 (shape observed from POST response).
_PARENT_AFTER_ADD = {
    "number": _PARENT,
    "id": 5400000001,
    "state": "closed",
    "sub_issues_summary": {"total": 1, "completed": 0, "percent_completed": 0},
}

# Minimal list response for GET .../sub_issues (numbers/states from probe).
_SUB_ISSUES_LIST = [
    {"number": 3033, "state": "closed", "id": 5407000506, "title": "child-a"},
    {"number": 3036, "state": "closed", "id": 5407000999, "title": "child-b"},
    {"number": 3037, "state": "closed", "id": 5407001000, "title": "child-c"},
]


def _http_error(code: int, message: str) -> urllib.error.HTTPError:
    body = json.dumps({"message": message}).encode("utf-8")
    return urllib.error.HTTPError(
        url="https://api.github.com/repos/o/r/issues/1/sub_issues",
        code=code,
        msg="error",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(body),
    )


def _ok_response(payload: Any) -> mock.MagicMock:
    raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
    resp = mock.MagicMock()
    resp.read.return_value = raw
    resp.headers = {
        "X-RateLimit-Remaining": "5000",
        "X-RateLimit-Limit": "5000",
        "X-RateLimit-Reset": "0",
    }
    resp.__enter__ = mock.MagicMock(return_value=resp)
    resp.__exit__ = mock.MagicMock(return_value=False)
    return resp


@pytest.fixture
def client() -> GitHubClient:
    return GitHubClient(token="tok", repo="sumipan/nexus")


def test_add_sub_issue_201_returns_parent_with_summary(
    client: GitHubClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST 201 → parent JSON; sub_issues_summary.total reflects the link."""
    calls: list[tuple[str, str, dict | None]] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> mock.MagicMock:
        method = req.get_method()
        url = req.full_url
        body = json.loads(req.data.decode()) if req.data else None
        calls.append((method, url, body))
        assert method == "POST"
        assert url.endswith(f"/issues/{_PARENT}/sub_issues")
        assert body == {"sub_issue_id": _CHILD_ID}
        return _ok_response(_PARENT_AFTER_ADD)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = client.add_sub_issue(_PARENT, _CHILD_ID)
    assert result is not None
    assert result["sub_issues_summary"]["total"] == 1
    assert calls and calls[0][2] == {"sub_issue_id": _CHILD_ID}


def test_add_sub_issue_422_duplicate_is_idempotent(
    client: GitHubClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second link of the same child → 422, no exception (idempotent success)."""

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> mock.MagicMock:
        raise _http_error(422, _DUP_MSG)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = client.add_sub_issue(_PARENT, _CHILD_ID)
    assert result is None


def test_add_sub_issue_403_wrong_id_raises_explicit_error(
    client: GitHubClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passing issue ``number`` as child_id → 403 with an explicit error message."""

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> mock.MagicMock:
        raise _http_error(403, _FORBIDDEN_MSG)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(PermissionDeniedError) as exc_info:
        # Deliberately pass number instead of id (probe: 403)
        client.add_sub_issue(_PARENT, _CHILD_NUMBER)
    msg = str(exc_info.value)
    assert "child_id" in msg
    assert exc_info.value.status_code == 403


def test_list_sub_issues_returns_number_state_id(
    client: GitHubClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_request(method: str, path: str, **kwargs: object) -> object:
        assert method == "GET"
        if kwargs.get("return_link_header"):
            return list(_SUB_ISSUES_LIST), None
        if path.endswith(f"/issues/{_PARENT}/sub_issues"):
            return list(_SUB_ISSUES_LIST)
        raise AssertionError(f"unexpected: {method} {path} {kwargs}")

    monkeypatch.setattr(client, "_request", fake_request)
    items = client.list_sub_issues(_PARENT)
    assert len(items) == 3
    for item in items:
        assert "number" in item
        assert "state" in item
        assert "id" in item
    assert {i["number"] for i in items} == {3033, 3036, 3037}


def test_sub_issues_summary_unlinked_returns_zeros(
    client: GitHubClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_request(method: str, path: str, **kwargs: object) -> dict:
        # Unlinked issue: API omits sub_issues_summary (probe: issue #1)
        return {"number": 1, "state": "open", "body": "x"}

    monkeypatch.setattr(client, "_request", fake_request)
    assert client.sub_issues_summary(1) == _EMPTY_SUMMARY


def test_sub_issues_summary_linked_returns_probe_shape(
    client: GitHubClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_request(method: str, path: str, **kwargs: object) -> dict:
        return {"number": _PARENT, "sub_issues_summary": dict(_REAL_SUMMARY)}

    monkeypatch.setattr(client, "_request", fake_request)
    assert client.sub_issues_summary(_PARENT) == _REAL_SUMMARY


def test_remove_sub_issue_deletes_with_child_id(
    client: GitHubClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method: str, path: str, **kwargs: object) -> dict | None:
        body = kwargs.get("body")
        assert isinstance(body, dict) or body is None
        calls.append((method, path, body if isinstance(body, dict) else None))
        return {"number": _PARENT, "sub_issues_summary": _EMPTY_SUMMARY}

    monkeypatch.setattr(client, "_request", fake_request)
    client.remove_sub_issue(_PARENT, _CHILD_ID)
    assert len(calls) == 1
    method, path, body = calls[0]
    assert method == "DELETE"
    # Real GitHub API uses singular ``sub_issue`` (not ``sub_issues``).
    assert path.endswith(f"/issues/{_PARENT}/sub_issue")
    assert body == {"sub_issue_id": _CHILD_ID}


def test_issue_get_fields_sub_issues_summary_default(
    client: GitHubClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``issue view --json sub_issues_summary`` relies on issue_get field filtering."""

    def fake_request(method: str, path: str, **kwargs: object) -> dict:
        return {"number": 1, "body": "x"}

    monkeypatch.setattr(client, "_request", fake_request)
    data = client.issue_get(1, fields=["sub_issues_summary"])
    assert data == {"sub_issues_summary": _EMPTY_SUMMARY}


def test_github_cli_issue_view_json_sub_issues_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ghdag.github_cli import cli_main

    class FakeClient:
        repo = "sumipan/nexus"

        def issue_get(self, number: int, fields: list[str] | None = None) -> dict:
            assert number == _PARENT
            assert fields == ["sub_issues_summary"]
            return {"sub_issues_summary": dict(_REAL_SUMMARY)}

    monkeypatch.setattr(
        "ghdag.github_cli.get_forge",
        lambda *a, **k: FakeClient(),
    )
    monkeypatch.setattr(
        "ghdag.github_cli.GitHubClient",
        lambda *a, **k: FakeClient(),
    )
    rc = cli_main(
        ["issue", "view", str(_PARENT), "--json", "sub_issues_summary"]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sub_issues_summary"] == _REAL_SUMMARY


def test_github_client_satisfies_port_with_sub_issue_methods() -> None:
    client = GitHubClient(token="tok", repo="o/r")
    assert isinstance(client, GitHubIssuePort)
    for name in (
        "add_sub_issue",
        "list_sub_issues",
        "remove_sub_issue",
        "sub_issues_summary",
    ):
        assert callable(getattr(client, name))
