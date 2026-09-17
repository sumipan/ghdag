"""Tests for GitHubClient rate-limit retry on 403 (nexus #3070).

On 403 + X-RateLimit-Remaining: 0:
  1. Prefer Retry-After when present
  2. Otherwise wait X-RateLimit-Reset - now
  3. If wait <= 900s, sleep and retry once
  4. If wait > 900s, or still 403 after retry, raise RateLimitError
"""

from __future__ import annotations

import io
import json
import time
import urllib.error
from unittest.mock import MagicMock

import pytest

from ghdag.exceptions import RateLimitError
from ghdag.github_client import GitHubClient


def _make_client() -> GitHubClient:
    return GitHubClient(token="token", repo="owner/repo")


def _mock_urlopen(payload: object, *, headers: dict | None = None):
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


def _http_error(code: int, *, headers: dict | None = None, message: str = "API rate limit exceeded"):
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


def test_rate_limit_waits_reset_then_retries_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Reset is now+60, wait 60s, retry, and succeed."""
    client = _make_client()
    now = 1_700_000_000
    monkeypatch.setattr(time, "time", lambda: now)
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))

    call_count = {"n": 0}

    def fake_urlopen(req, timeout=120):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _http_error(
                403,
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(now + 60),
                },
            )
        return _mock_urlopen(
            {"number": 1},
            headers={
                "X-RateLimit-Remaining": "4999",
                "X-RateLimit-Limit": "5000",
                "X-RateLimit-Used": "1",
                "X-RateLimit-Reset": str(now + 3600),
            },
        )

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    result = client._request("GET", "/repos/owner/repo/issues/1")
    assert result == {"number": 1}
    assert slept == [60.0]
    assert call_count["n"] == 2


def test_rate_limit_over_15_min_raises_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Reset is now+901, raise RateLimitError immediately (no sleep)."""
    client = _make_client()
    now = 1_700_000_000
    monkeypatch.setattr(time, "time", lambda: now)
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))

    def fake_urlopen(req, timeout=120):
        raise _http_error(
            403,
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(now + 901),
            },
        )

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RateLimitError) as exc_info:
        client._request("GET", "/repos/owner/repo/issues/1")
    assert exc_info.value.status_code == 403
    assert exc_info.value.reset_at == now + 901
    assert slept == []


def test_retry_after_preferred_over_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Retry-After: 30 is present, prefer it over X-RateLimit-Reset and wait 30s."""
    client = _make_client()
    now = 1_700_000_000
    monkeypatch.setattr(time, "time", lambda: now)
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))

    call_count = {"n": 0}

    def fake_urlopen(req, timeout=120):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _http_error(
                403,
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(now + 600),
                    "Retry-After": "30",
                },
            )
        return _mock_urlopen({"ok": True})

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    result = client._request("GET", "/repos/owner/repo/issues/1")
    assert result == {"ok": True}
    assert slept == [30.0]
    assert call_count["n"] == 2


def test_rate_limit_retry_still_403_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """If still 403 after retry, raise RateLimitError."""
    client = _make_client()
    now = 1_700_000_000
    monkeypatch.setattr(time, "time", lambda: now)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    def fake_urlopen(req, timeout=120):
        raise _http_error(
            403,
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(now + 10),
            },
        )

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RateLimitError) as exc_info:
        client._request("GET", "/repos/owner/repo/issues/1")
    assert exc_info.value.reset_at == now + 10
