"""Tests for GitHubClient ETag conditional requests (nexus #3070).

Measured headers (2026-09-10, GET /repos/sumipan/nexus/issues?state=open&per_page=1):
  ETag: '"fe991d6a950abb80cb7059ab6aaaca5636b5be168ac6fa515f1ab09de77934e8"'
  X-RateLimit-Remaining: '2474'
  X-RateLimit-Limit: '5000'
  X-RateLimit-Used: '2526'
  X-RateLimit-Reset: '1789023299'
304 returns the cached body without consuming rate limit.
"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock

import pytest

from ghdag.github_client import GitHubClient

# Measured ETag (strong validator, including surrounding double quotes)
_REALISTIC_ETAG = '"fe991d6a950abb80cb7059ab6aaaca5636b5be168ac6fa515f1ab09de77934e8"'


def _make_client() -> GitHubClient:
    return GitHubClient(token="token", repo="owner/repo")


def _mock_urlopen(payload: object, *, status: int = 200, headers: dict | None = None):
    body = json.dumps(payload).encode()
    hdrs = headers or {}

    class _FakeResp:
        def __init__(self) -> None:
            self.headers = hdrs
            self.status = status

        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    return _FakeResp()


def _http_error(code: int, *, headers: dict | None = None, message: str = "error"):
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


def test_304_returns_cached_body_without_updating_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On HTTP 304, return cached body and do not update _last_rate_limit."""
    client = _make_client()
    call_count = {"n": 0}

    def fake_urlopen(req, timeout=120):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _mock_urlopen(
                [{"number": 42, "title": "cached"}],
                headers={
                    "ETag": _REALISTIC_ETAG,
                    "X-RateLimit-Remaining": "100",
                    "X-RateLimit-Limit": "5000",
                    "X-RateLimit-Used": "4900",
                    "X-RateLimit-Reset": "1700003600",
                },
            )
        raise _http_error(304, headers={"ETag": _REALISTIC_ETAG})

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)

    client._request("GET", "/repos/owner/repo/issues", etag=True)
    assert client.get_last_rate_limit() is not None
    assert client.get_last_rate_limit()["remaining"] == 100

    # 304 path — remaining stays at 100
    result = client._request("GET", "/repos/owner/repo/issues", etag=True)
    assert result == [{"number": 42, "title": "cached"}]
    assert client.get_last_rate_limit()["remaining"] == 100


def test_list_all_issues_uses_etag(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_all_issues calls _request with etag=True."""
    client = _make_client()
    seen: list[bool] = []

    def fake_request(method, path, **kwargs):
        seen.append(bool(kwargs.get("etag")))
        if kwargs.get("return_link_header"):
            return [{"number": 1, "labels": []}], None
        return [{"number": 1, "labels": []}]

    monkeypatch.setattr(client, "_request", fake_request)
    result = client.list_all_issues("open")
    assert result == [{"number": 1, "labels": []}]
    assert any(seen)
