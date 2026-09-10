"""Tests for GitHubClient ETag conditional requests (nexus #3070).

実測ヘッダ（2026-09-10, GET /repos/sumipan/nexus/issues?state=open&per_page=1）:
  ETag: '"fe991d6a950abb80cb7059ab6aaaca5636b5be168ac6fa515f1ab09de77934e8"'
  X-RateLimit-Remaining: '2474'
  X-RateLimit-Limit: '5000'
  X-RateLimit-Used: '2526'
  X-RateLimit-Reset: '1789023299'
304 はレート消費なしでキャッシュ本文を返す。
"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock

import pytest

from ghdag.github_client import GitHubClient

# 実測 ETag（strong validator、両端の二重引用符を含む）
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


def test_etag_true_sends_if_none_match_on_second_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """2 回目以降の etag=True リクエストに If-None-Match が付く。"""
    client = _make_client()
    captured_headers: list[dict[str, str]] = []
    call_count = {"n": 0}

    def fake_urlopen(req, timeout=120):
        call_count["n"] += 1
        captured_headers.append(dict(req.headers))
        if call_count["n"] == 1:
            return _mock_urlopen(
                [{"number": 1}],
                headers={
                    "ETag": _REALISTIC_ETAG,
                    "X-RateLimit-Remaining": "4999",
                    "X-RateLimit-Limit": "5000",
                    "X-RateLimit-Used": "1",
                    "X-RateLimit-Reset": "1700003600",
                },
            )
        raise _http_error(304, headers={"ETag": _REALISTIC_ETAG})

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)

    first = client._request("GET", "/repos/owner/repo/issues", etag=True)
    second = client._request("GET", "/repos/owner/repo/issues", etag=True)

    assert first == [{"number": 1}]
    assert second == [{"number": 1}]
    assert call_count["n"] == 2
    # 1 回目は If-None-Match なし、2 回目はキャッシュ ETag を送る
    assert "If-none-match" not in {k.lower(): v for k, v in captured_headers[0].items()} or (
        captured_headers[0].get("If-None-Match") is None
        and captured_headers[0].get("If-none-match") is None
    )
    second_hdrs = {k.lower(): v for k, v in captured_headers[1].items()}
    assert second_hdrs.get("if-none-match") == _REALISTIC_ETAG


def test_304_returns_cached_body_without_updating_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 304 時はキャッシュ本文を返し、_last_rate_limit を更新しない。"""
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

    # 304 経路 — remaining は 100 のまま
    result = client._request("GET", "/repos/owner/repo/issues", etag=True)
    assert result == [{"number": 42, "title": "cached"}]
    assert client.get_last_rate_limit()["remaining"] == 100


def test_200_stores_etag_in_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """200 応答時に ETag をインスタンスキャッシュへ保存する。"""
    client = _make_client()

    def fake_urlopen(req, timeout=120):
        return _mock_urlopen(
            [{"number": 7}],
            headers={"ETag": _REALISTIC_ETAG},
        )

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    client._request("GET", "/repos/owner/repo/issues", etag=True)

    assert len(client._etag_cache) == 1
    etag, body = next(iter(client._etag_cache.values()))
    assert etag == _REALISTIC_ETAG
    assert body == [{"number": 7}]


def test_etag_false_does_not_send_if_none_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """etag=False（既定）ではキャッシュがあっても If-None-Match を付けない。"""
    client = _make_client()
    client._etag_cache["https://api.github.com/repos/owner/repo/issues"] = (
        _REALISTIC_ETAG,
        [{"number": 1}],
    )
    captured: list[dict[str, str]] = []

    def fake_urlopen(req, timeout=120):
        captured.append(dict(req.headers))
        return _mock_urlopen([{"number": 2}], headers={"ETag": 'W/"other"'})

    monkeypatch.setattr("ghdag.github_client.urllib.request.urlopen", fake_urlopen)
    result = client._request("GET", "/repos/owner/repo/issues", etag=False)
    assert result == [{"number": 2}]
    hdrs = {k.lower(): v for k, v in captured[0].items()}
    assert "if-none-match" not in hdrs


def test_list_all_issues_uses_etag(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_all_issues は etag=True で _request する。"""
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
