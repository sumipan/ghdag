"""Tests for GitHubClient disk-persistent ETag cache (nexus #3761).

Callers (issuesmith queue tick, ``ghdag watch --once``) run as separate
processes, so the in-memory ETag cache starts empty every time. With
``etag_cache_path`` / ``GHDAG_ETAG_CACHE`` the cache survives across
instances and 304 responses avoid consuming the rate limit.
"""

from __future__ import annotations

import hashlib
import io
import json
import urllib.error
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from ghdag import github_client as gc_mod
from ghdag.github_client import GitHubClient

_ETAG = '"fe991d6a950abb80cb7059ab6aaaca5636b5be168ac6fa515f1ab09de77934e8"'
_URL = "https://api.github.com/repos/owner/repo/issues?state=open&per_page=100"


class _FakeResp:
    def __init__(self, payload: object, headers: dict[str, str]) -> None:
        self._body = json.dumps(payload).encode()
        self.headers = headers

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *args: object) -> None:
        pass


def _http_304() -> urllib.error.HTTPError:
    hdrs = MagicMock()
    hdrs.get = lambda k, d=None: {"X-RateLimit-Remaining": "4999"}.get(k, d)
    return urllib.error.HTTPError(
        url=_URL, code=304, msg="Not Modified", hdrs=hdrs, fp=io.BytesIO(b"")
    )


def _rate_headers(remaining: str, etag: str | None = _ETAG) -> dict[str, str]:
    h = {
        "X-RateLimit-Remaining": remaining,
        "X-RateLimit-Limit": "5000",
        "X-RateLimit-Reset": "1789023299",
    }
    if etag:
        h["ETag"] = etag
    return h


def _install_urlopen(
    monkeypatch: pytest.MonkeyPatch, responses: list[Any]
) -> list[dict[str, str]]:
    """Patch urlopen to return/raise *responses* in order; returns captured headers."""
    captured: list[dict[str, str]] = []
    queue = list(responses)

    def fake_urlopen(req: Any, timeout: int = 120) -> Any:
        captured.append(dict(req.headers))
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(gc_mod.urllib.request, "urlopen", fake_urlopen)
    return captured


def _has_inm(headers: dict[str, str]) -> bool:
    # urllib.request.Request capitalizes header names ("If-none-match").
    return any(k.lower() == "if-none-match" for k in headers)


@pytest.fixture(autouse=True)
def _no_env_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GHDAG_ETAG_CACHE", raising=False)




def test_304_response_preserves_rate_limit_remaining(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "etag.json"
    _install_urlopen(
        monkeypatch,
        [
            _FakeResp([{"number": 1}], _rate_headers("4000")),
            _http_304(),
        ],
    )
    client = GitHubClient(token="tok", repo="owner/repo", etag_cache_path=cache)
    client._request("GET", _URL, etag=True)
    before = client.get_last_rate_limit()
    assert before is not None and before["remaining"] == 4000

    client._request("GET", _URL, etag=True)
    after = client.get_last_rate_limit()
    assert after is not None and after["remaining"] == 4000



@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"k": "v"}', ""])
def test_corrupt_cache_file_triggers_fresh_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str
) -> None:
    cache = tmp_path / "etag.json"
    cache.write_text(content)
    payload = [{"number": 3}]
    captured = _install_urlopen(
        monkeypatch, [_FakeResp(payload, _rate_headers("4999"))]
    )

    client = GitHubClient(token="tok", repo="owner/repo", etag_cache_path=cache)
    assert client._request("GET", _URL, etag=True) == payload
    assert not _has_inm(captured[0])
    # The corrupt file is replaced with a valid cache.
    assert len(json.loads(cache.read_text())) == 1



def test_lru_eviction_keeps_cache_at_max_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "etag.json"
    token_hash = hashlib.sha256(b"tok").hexdigest()[:8]
    seed = {
        f"{token_hash}:https://api.github.com/x/{i}": {
            "etag": f'"e{i}"',
            "body": {"i": i},
            "accessed": 100.0 + i,
        }
        for i in range(2000)
    }
    cache.write_text(json.dumps(seed))
    monkeypatch.setattr(gc_mod.time, "time", lambda: 999999.0)
    _install_urlopen(monkeypatch, [_FakeResp({"new": True}, _rate_headers("4999"))])

    client = GitHubClient(token="tok", repo="owner/repo", etag_cache_path=cache)
    client._request("GET", _URL, etag=True)

    data = json.loads(cache.read_text())
    assert len(data) <= 2000
    assert f"{token_hash}:{_URL}" in data
    # The oldest entry (accessed=100.0) was evicted.
    assert f"{token_hash}:https://api.github.com/x/0" not in data
    assert f"{token_hash}:https://api.github.com/x/1999" in data
    assert not list(tmp_path.glob("*.tmp.*"))


def test_different_token_does_not_reuse_cache_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "etag.json"
    captured = _install_urlopen(
        monkeypatch,
        [
            _FakeResp([{"number": 1}], _rate_headers("4999")),
            _FakeResp([{"number": 1}], _rate_headers("4998")),
        ],
    )
    GitHubClient(token="tok-a", repo="owner/repo", etag_cache_path=cache)._request(
        "GET", _URL, etag=True
    )
    GitHubClient(token="tok-b", repo="owner/repo", etag_cache_path=cache)._request(
        "GET", _URL, etag=True
    )

    assert not _has_inm(captured[1])
    # Both tokens' entries coexist in the shared file.
    assert len(json.loads(cache.read_text())) == 2


