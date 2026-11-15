"""Tests for ETag on every GET and the batched, merging disk flush.

issuesmith's tick reads issues, comments, PRs and sub-issues every minute; only
``list_all_issues`` used conditional requests, so each tick spent hundreds of
calls of the 5000/hour PAT budget re-reading unchanged resources. A 304 does
not count against the rate limit.
"""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any
from unittest.mock import MagicMock

import pytest

from ghdag import github_client as gc_mod
from ghdag.github_client import GitHubClient

_ETAG = 'W/"abc"'


class _FakeResp:
    def __init__(self, payload: object, etag: str | None = _ETAG) -> None:
        self._body = json.dumps(payload).encode()
        self.headers = {"X-RateLimit-Remaining": "4999"}
        if etag:
            self.headers["ETag"] = etag

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *args: object) -> None:
        pass


def _http_304() -> urllib.error.HTTPError:
    hdrs = MagicMock()
    hdrs.get = lambda k, d=None: d
    return urllib.error.HTTPError(
        url="x", code=304, msg="Not Modified", hdrs=hdrs, fp=io.BytesIO(b"")
    )


def _install_urlopen(
    monkeypatch: pytest.MonkeyPatch, responses: list[Any]
) -> list[Any]:
    captured: list[Any] = []
    queue = list(responses)

    def fake_urlopen(req: Any, timeout: int = 120) -> Any:
        captured.append(req)
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(gc_mod.urllib.request, "urlopen", fake_urlopen)
    return captured


def _inm(req: Any) -> str | None:
    return {k.lower(): v for k, v in req.headers.items()}.get("if-none-match")


@pytest.fixture(autouse=True)
def _no_env_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GHDAG_ETAG_CACHE", raising=False)


def test_issue_get_sends_if_none_match_and_uses_304_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue = {"number": 7, "state": "open", "labels": [{"name": "design:done"}]}
    captured = _install_urlopen(monkeypatch, [_FakeResp(issue), _http_304()])
    client = GitHubClient(token="tok", repo="owner/repo")

    first = client.issue_get(7, fields=["state", "labels"])
    second = client.issue_get(7, fields=["state", "labels"])

    assert first == second == {"state": "OPEN", "labels": [{"name": "design:done"}]}
    assert _inm(captured[0]) is None
    assert _inm(captured[1]) == _ETAG


def test_paginated_reads_use_etag(monkeypatch: pytest.MonkeyPatch) -> None:
    comments = [{"user": {"login": "a"}, "created_at": "t", "body": "b"}]
    captured = _install_urlopen(monkeypatch, [_FakeResp(comments), _http_304()])
    client = GitHubClient(token="tok", repo="owner/repo")

    assert client.get_issue_comments(1) == client.get_issue_comments(1)
    assert _inm(captured[1]) == _ETAG


def test_raw_get_does_not_use_etag(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _install_urlopen(monkeypatch, [_FakeResp("d"), _FakeResp("d")])
    client = GitHubClient(token="tok", repo="owner/repo")

    client._request("GET", "/repos/owner/repo/pulls/1", raw=True)
    client._request("GET", "/repos/owner/repo/pulls/1", raw=True)

    assert _inm(captured[1]) is None


