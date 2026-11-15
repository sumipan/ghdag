"""Unit tests for ghdag.github_client (urllib-based GitHubClient)."""

from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.parse
from unittest import mock

import pytest

from ghdag.github_client import (
    API_BASE,
    GRAPHQL_URL,
    GitHubClient,
    _resolve_repo,
)


def test_module_exports_importable() -> None:
    from ghdag.github_client import GitHubClient as GC  # noqa: F401

    assert API_BASE == "https://api.github.com"
    assert GRAPHQL_URL == "https://api.github.com/graphql"


def test_default_repo_is_not_exposed() -> None:
    import ghdag.forge
    import ghdag.github_client

    with pytest.raises(ImportError):
        from ghdag.github_client import DEFAULT_REPO  # noqa: F401

    with pytest.raises(ImportError):
        from ghdag.forge import DEFAULT_REPO  # noqa: F401

    assert not hasattr(ghdag.github_client, "DEFAULT_REPO")
    assert "DEFAULT_REPO" not in ghdag.forge.__all__
    assert not hasattr(ghdag.forge, "DEFAULT_REPO")


def test_client_repo_property() -> None:
    client = GitHubClient(token="t", repo="o/r")
    assert client.repo == "o/r"


def test_user_agent_header() -> None:
    client = GitHubClient(token="t", repo="o/r")
    assert client._headers()["User-Agent"] == "ghdag-github-client"


def test_resolve_repo_invalid_format() -> None:
    with pytest.raises(ValueError, match="Invalid repo format"):
        _resolve_repo("not-a-repo")


def test_resolve_repo_missing_env_raises_ghdag_error() -> None:
    """GITHUB_REPOSITORIES unset and repo=None → GhdagError (no implicit default repository)."""
    from ghdag.core.exceptions import GhdagError

    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(
            GhdagError,
            match="GITHUB_REPOSITORIES is not set and no repo was specified",
        ):
            _resolve_repo(None)
        with pytest.raises(
            GhdagError,
            match="GITHUB_REPOSITORIES is not set and no repo was specified",
        ):
            GitHubClient(token="t")


def test_resolve_repo_from_github_repositories_env() -> None:
    with mock.patch.dict(
        os.environ, {"GITHUB_REPOSITORIES": "acme/widgets,other/repo"}, clear=False
    ):
        assert _resolve_repo(None) == ("acme", "widgets")


# Real Link header shape (2026-09-10 probe via urllib against api.github.com):
#   '<https://api.github.com/repositories/1206558072/issues?per_page=1&state=all&after=...&page=2>; rel="next"'
_REAL_LINK_NEXT = (
    '<https://api.github.com/repositories/1206558072/issues?'
    'per_page=1&state=all&after=Y3Vyc29yOnYyOpLPAAABoIdganDPAAAAAUINI3I%3D&page=2>; '
    'rel="next"'
)
_NEXT_URL = (
    "https://api.github.com/repositories/1206558072/issues?"
    "per_page=1&state=all&after=Y3Vyc29yOnYyOpLPAAABoIdganDPAAAAAUINI3I%3D&page=2"
)


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

    def fake_request(method: str, path: str, **kwargs: object) -> list | dict | tuple:
        if method == "GET" and "/issues/1" in path and "comments" not in path:
            return {"number": 1}
        if method == "GET" and "comments" in path:
            data: list = [
                {
                    "body": "hi",
                    "user": {"login": "alice"},
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ]
            if kwargs.get("return_link_header"):
                return data, None
            return data
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


def test_pr_create_slash_branch_builds_owner_head_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    """feat/xxx branch (contains /) still gets the owner: prefix."""
    client = GitHubClient(token="tok", repo="o/r")
    captured: dict[str, object] = {}

    def fake_request(method: str, path: str, **kwargs: object) -> dict:
        captured["body"] = kwargs.get("body")
        return {"html_url": "https://github.com/o/r/pull/2"}

    monkeypatch.setattr(client, "_request", fake_request)
    client.pr_create("main", "feat/issue-1759-abcd1234", "title", "body")
    assert captured["body"] == {
        "title": "title",
        "body": "body",
        "head": "o:feat/issue-1759-abcd1234",
        "base": "main",
    }


def test_pr_list_slash_branch_passes_owner_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """pr_list on feat/xxx passes owner:branch form to the API."""
    client = GitHubClient(token="tok", repo="o/r")
    captured: dict[str, object] = {}

    def fake_request(method: str, path: str, **kwargs: object) -> object:
        captured["path"] = path
        if kwargs.get("return_link_header"):
            return [], None
        return []

    monkeypatch.setattr(client, "_request", fake_request)
    client.pr_list(head="feat/issue-1759-abcd1234")
    path = str(captured.get("path", ""))
    qs = urllib.parse.parse_qs(path.split("?", 1)[1] if "?" in path else "")
    assert qs.get("head", [""])[0] == "o:feat/issue-1759-abcd1234"


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


# --- limited retry on transient faults + label idempotent convergence (nexus#2563) ---


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ghdag.github_client.time.sleep", lambda *_: None)


def test_transient_exhausted_raises_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import http.client

    from ghdag.core.exceptions import NetworkError

    _no_sleep(monkeypatch)
    client = GitHubClient(token="tok", repo="o/r")
    calls: list[int] = []

    def fake_urlopen(req: object, timeout: int = 120) -> None:
        calls.append(1)
        raise http.client.RemoteDisconnected("gone")

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(NetworkError):
        client._request("GET", "/repos/o/r/issues/1")
    assert len(calls) == 3


def test_http_502_retried_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_sleep(monkeypatch)
    client = GitHubClient(token="tok", repo="o/r")
    calls: list[int] = []

    class FakeResp:
        def read(self) -> bytes:
            return json.dumps({"ok": True}).encode()

        def __enter__(self) -> "FakeResp":
            return self

        def __exit__(self, *args: object) -> None:
            pass

    def fake_urlopen(req: object, timeout: int = 120) -> FakeResp:
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                url="https://api.github.com/x",
                code=502,
                msg="Bad Gateway",
                hdrs=mock.MagicMock(**{"get.return_value": None}),
                fp=io.BytesIO(b"{}"),
            )
        return FakeResp()

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    assert client._request("GET", "/repos/o/r/issues/1") == {"ok": True}
    assert len(calls) == 2


def test_issue_update_tolerates_delete_404(monkeypatch: pytest.MonkeyPatch) -> None:
    from ghdag.core.exceptions import GitHubApiError

    client = GitHubClient(token="tok", repo="o/r")
    seen: list[tuple[str, str]] = []

    def fake_request(method: str, path: str, **kwargs: object) -> None:
        seen.append((method, path))
        if method == "DELETE":
            raise GitHubApiError("Label does not exist", status_code=404)
        return None

    monkeypatch.setattr(client, "_request", fake_request)
    client.issue_update(1, labels_remove=["old"], labels_add=["new"])
    assert ("DELETE", "/repos/o/r/issues/1/labels/old") in seen
    assert ("POST", "/repos/o/r/issues/1/labels") in seen


def test_issue_update_converges_when_target_already_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST already applied and only response lost → refetch confirms target state as success"""
    from ghdag.core.exceptions import NetworkError

    client = GitHubClient(token="tok", repo="o/r")

    def fake_request(method: str, path: str, **kwargs: object) -> None:
        if method == "POST":
            raise NetworkError("Network error: connection reset")
        return None

    monkeypatch.setattr(client, "_request", fake_request)
    monkeypatch.setattr(
        client, "issue_get", lambda n, fields=None: {"labels": [{"name": "new"}]}
    )
    client.issue_update(1, labels_remove=["old"], labels_add=["new"])  # must not raise


def test_issue_update_reraises_when_not_converged(monkeypatch: pytest.MonkeyPatch) -> None:
    from ghdag.core.exceptions import NetworkError

    client = GitHubClient(token="tok", repo="o/r")

    def fake_request(method: str, path: str, **kwargs: object) -> None:
        if method == "DELETE":
            raise NetworkError("down")
        return None

    monkeypatch.setattr(client, "_request", fake_request)
    monkeypatch.setattr(
        client, "issue_get", lambda n, fields=None: {"labels": [{"name": "old"}]}
    )
    with pytest.raises(NetworkError):
        client.issue_update(1, labels_remove=["old"], labels_add=["new"])


# --- rate_limit_max_wait_sec / GHDAG_RATE_LIMIT_MAX_WAIT_SEC (nexus #3752) ---


def _rate_limited_error(reset_at: int) -> urllib.error.HTTPError:
    hdrs = mock.MagicMock()
    values = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset_at)}
    hdrs.get = lambda k, d=None: values.get(k, d)
    return urllib.error.HTTPError(
        url="https://api.github.com/x",
        code=403,
        msg="Forbidden",
        hdrs=hdrs,
        fp=io.BytesIO(json.dumps({"message": "API rate limit exceeded"}).encode()),
    )


class _OkResp:
    headers: dict = {}

    def read(self) -> bytes:
        return b'{"ok": true}'

    def __enter__(self) -> _OkResp:
        return self

    def __exit__(self, *args: object) -> None:
        pass


def _fake_rate_limited_then_ok(reset_at: int, calls: list[int]):
    def fake_urlopen(req: object, timeout: int = 120) -> _OkResp:
        calls.append(1)
        if len(calls) == 1:
            raise _rate_limited_error(reset_at)
        return _OkResp()

    return fake_urlopen


def test_rate_limit_max_wait_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-4: GHDAG_RATE_LIMIT_MAX_WAIT_SEC=5 is used as the limit."""
    import time

    from ghdag.core.exceptions import RateLimitError

    monkeypatch.setenv("GHDAG_RATE_LIMIT_MAX_WAIT_SEC", "5")
    now = 1_700_000_000
    monkeypatch.setattr(time, "time", lambda: now)
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    client = GitHubClient(token="tok", repo="o/r")
    assert client._rate_limit_max_wait_sec == 5
    calls: list[int] = []
    monkeypatch.setattr(
        "ghdag.github_client.urllib.request.urlopen",
        _fake_rate_limited_then_ok(now + 6, calls),
    )

    with pytest.raises(RateLimitError):
        client._request("GET", "/repos/o/r/issues/1")
    assert slept == []
    assert len(calls) == 1


def test_rate_limit_max_wait_default_when_env_unset_or_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GHDAG_RATE_LIMIT_MAX_WAIT_SEC", raising=False)
    assert GitHubClient(token="tok", repo="o/r")._rate_limit_max_wait_sec == 900
    monkeypatch.setenv("GHDAG_RATE_LIMIT_MAX_WAIT_SEC", "abc")
    assert GitHubClient(token="tok", repo="o/r")._rate_limit_max_wait_sec == 900


class _GraphqlResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.headers: dict[str, str] = {}

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self) -> _GraphqlResp:
        return self

    def __exit__(self, *args: object) -> None:
        pass


def test_graphql_passes_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    client = GitHubClient(token="tok", repo="o/r")
    captured: list[object] = []

    def fake_urlopen(req: object, timeout: int = 120) -> _GraphqlResp:
        captured.append(req)
        return _GraphqlResp({"data": None})

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    assert client.graphql("query($n: Int!) { x(n: $n) }", {"n": 1}) == {}
    payload = json.loads(captured[0].data.decode())  # type: ignore[attr-defined]
    assert payload["variables"] == {"n": 1}


def test_graphql_errors_raise_github_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from ghdag.core.exceptions import GitHubApiError

    client = GitHubClient(token="tok", repo="o/r")
    monkeypatch.setattr(
        "ghdag.github_client.urllib.request.urlopen",
        lambda req, timeout=120: _GraphqlResp({"errors": [{"message": "boom"}]}),
    )
    with pytest.raises(GitHubApiError, match="boom"):
        client.graphql("query { x }")


def _rate_limited_http_error(reset: str | None = None) -> urllib.error.HTTPError:
    body = json.dumps({"message": "rate limit exceeded"}).encode()
    values = {"X-RateLimit-Remaining": "0"}
    if reset is not None:
        values["X-RateLimit-Reset"] = reset
    hdrs = mock.MagicMock()
    hdrs.get = lambda k, d=None: values.get(k, d)
    return urllib.error.HTTPError(
        url=GRAPHQL_URL, code=403, msg="Forbidden", hdrs=hdrs, fp=io.BytesIO(body)
    )


def test_graphql_rate_limit_beyond_wait_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import time as _time

    from ghdag.core.exceptions import RateLimitError

    client = GitHubClient(token="tok", repo="o/r", rate_limit_max_wait_sec=1)
    reset = str(int(_time.time()) + 3600)

    def fake_urlopen(req: object, timeout: int = 120) -> None:
        raise _rate_limited_http_error(reset)

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RateLimitError) as exc_info:
        client.graphql("query { ok }")
    assert exc_info.value.reset_at == int(reset)
