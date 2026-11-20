"""Tests for GitHubClient disk-persistent ETag cache (nexus #3761, sumipan/ghdag#7466).

Each cache entry is stored as its own JSON file under ``etag_cache_path`` so
processes do not load the full cache into memory at startup.
"""

from __future__ import annotations

import builtins
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
_URL1 = "https://api.github.com/repos/owner/repo/issues?state=open&per_page=100"
_URL2 = "https://api.github.com/repos/owner/repo/issues/7"
_TOK = "tok"


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
        url=_URL1, code=304, msg="Not Modified", hdrs=hdrs, fp=io.BytesIO(b"")
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
    return any(k.lower() == "if-none-match" for k in headers)


def _disk_key(token: str, url: str) -> str:
    prefix = hashlib.sha256(token.encode()).hexdigest()[:8] + ":"
    return prefix + url


def _entry_path(cache_dir: Path, token: str, url: str) -> Path:
    key = _disk_key(token, url)
    name = hashlib.sha256(key.encode()).hexdigest() + ".json"
    return cache_dir / name


def _write_entry(
    cache_dir: Path, token: str, url: str, *, etag: str, body: Any
) -> Path:
    path = _entry_path(cache_dir, token, url)
    key = _disk_key(token, url)
    path.write_text(
        json.dumps({"key": key, "etag": etag, "body": body}), encoding="utf-8"
    )
    return path


@pytest.fixture(autouse=True)
def _no_env_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GHDAG_ETAG_CACHE", raising=False)


def test_304_response_preserves_rate_limit_remaining(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "c"
    _install_urlopen(
        monkeypatch,
        [
            _FakeResp([{"number": 1}], _rate_headers("4000")),
            _http_304(),
        ],
    )
    client = GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)
    client._request("GET", _URL1, etag=True)
    before = client.get_last_rate_limit()
    assert before is not None and before["remaining"] == 4000

    client._request("GET", _URL1, etag=True)
    after = client.get_last_rate_limit()
    assert after is not None and after["remaining"] == 4000


def test_new_client_reuses_entry_written_by_other_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "c"
    body1 = [{"number": 1}]
    body2 = {"number": 7}
    captured = _install_urlopen(
        monkeypatch,
        [
            _FakeResp(body1, _rate_headers("4999")),
            _FakeResp(body2, _rate_headers("4998")),
            _http_304(),
        ],
    )
    GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)._request(
        "GET", _URL1, etag=True
    )
    GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)._request(
        "GET", _URL2, etag=True
    )
    client_b = GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)
    assert client_b._request("GET", _URL1, etag=True) == body1
    assert _has_inm(captured[2])
    assert captured[2].get("If-None-Match") == _ETAG or any(
        v == _ETAG for k, v in captured[2].items() if k.lower() == "if-none-match"
    )


def test_init_reads_no_entry_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cache = tmp_path / "c"
    cache.mkdir()
    for url in (_URL1, _URL2, "https://api.github.com/x"):
        _write_entry(cache, _TOK, url, etag=_ETAG, body={"n": url})

    counts = {"open": 0, "read_text": 0, "read_bytes": 0}
    real_open = builtins.open
    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes

    def counting_open(*args: Any, **kwargs: Any) -> Any:
        counts["open"] += 1
        return real_open(*args, **kwargs)

    def counting_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        counts["read_text"] += 1
        return real_read_text(self, *args, **kwargs)

    def counting_read_bytes(self: Path, *args: Any, **kwargs: Any) -> bytes:
        counts["read_bytes"] += 1
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", counting_open)
    monkeypatch.setattr(Path, "read_text", counting_read_text)
    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)

    assert counts == {"open": 0, "read_text": 0, "read_bytes": 0}


def test_304_returns_body_from_entry_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "c"
    edited = {"edited": True}
    _install_urlopen(
        monkeypatch,
        [_FakeResp([{"number": 1}], _rate_headers("4999")), _http_304()],
    )
    client = GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)
    client._request("GET", _URL1, etag=True)
    path = _entry_path(cache, _TOK, _URL1)
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    on_disk["body"] = edited
    path.write_text(json.dumps(on_disk), encoding="utf-8")

    assert client._request("GET", _URL1, etag=True) == edited


def test_corrupt_other_entry_does_not_affect_lookup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "c"
    body1 = [{"number": 1}]
    body2 = {"number": 2}
    _install_urlopen(
        monkeypatch,
        [
            _FakeResp(body1, _rate_headers("4999")),
            _FakeResp(body2, _rate_headers("4998")),
            _http_304(),
        ],
    )
    GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)._request(
        "GET", _URL1, etag=True
    )
    GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)._request(
        "GET", _URL2, etag=True
    )
    _entry_path(cache, _TOK, _URL2).write_text("{not json", encoding="utf-8")
    client = GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)
    assert client._request("GET", _URL1, etag=True) == body1


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        json.dumps([1, 2]),
        json.dumps({"key": "wrong", "etag": _ETAG, "body": {}}),
    ],
)
def test_corrupt_entry_is_refetched_and_rewritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str
) -> None:
    cache = tmp_path / "c"
    cache.mkdir()
    path = _entry_path(cache, _TOK, _URL1)
    path.write_text(content, encoding="utf-8")
    payload = [{"number": 3}]
    captured = _install_urlopen(
        monkeypatch, [_FakeResp(payload, _rate_headers("4999")), _http_304()]
    )
    client = GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)
    assert client._request("GET", _URL1, etag=True) == payload
    assert not _has_inm(captured[0])

    client2 = GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)
    client2._request("GET", _URL1, etag=True)
    assert _has_inm(captured[1])


def test_304_touches_only_that_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "c"
    _install_urlopen(
        monkeypatch,
        [
            _FakeResp({"a": 1}, _rate_headers("4999")),
            _FakeResp({"b": 2}, _rate_headers("4998")),
            _http_304(),
        ],
    )
    client = GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)
    client._request("GET", _URL1, etag=True)
    client._request("GET", _URL2, etag=True)
    path1 = _entry_path(cache, _TOK, _URL1)
    path2 = _entry_path(cache, _TOK, _URL2)
    old_time = 1_000_000.0
    path1.write_bytes(path1.read_bytes())
    path2.write_bytes(path2.read_bytes())
    import os

    os.utime(path1, (old_time, old_time))
    os.utime(path2, (old_time, old_time))
    bytes1_before = path1.read_bytes()
    bytes2_before = path2.read_bytes()
    mtime2_before = path2.stat().st_mtime

    client._request("GET", _URL1, etag=True)

    assert path1.read_bytes() == bytes1_before
    assert path1.stat().st_mtime > old_time
    assert path2.read_bytes() == bytes2_before
    assert path2.stat().st_mtime == mtime2_before


def test_304_after_entry_deleted_mid_request_returns_loaded_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "c"
    body = [{"number": 9}]
    path_holder: list[Path] = []

    def fake_urlopen(req: Any, timeout: int = 120) -> Any:
        if not path_holder:
            return _FakeResp(body, _rate_headers("4999"))
        path_holder[0].unlink()
        raise _http_304()

    monkeypatch.setattr(gc_mod.urllib.request, "urlopen", fake_urlopen)
    client = GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)
    client._request("GET", _URL1, etag=True)
    path_holder.append(_entry_path(cache, _TOK, _URL1))
    assert client._request("GET", _URL1, etag=True) == body


def test_two_clients_keep_both_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "c"
    _install_urlopen(
        monkeypatch,
        [
            _FakeResp({"u": 1}, _rate_headers("4999")),
            _FakeResp({"u": 2}, _rate_headers("4998")),
            _http_304(),
            _http_304(),
        ],
    )
    a = GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)
    b = GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)
    a._request("GET", _URL1, etag=True)
    b._request("GET", _URL2, etag=True)
    assert _entry_path(cache, _TOK, _URL1).is_file()
    assert _entry_path(cache, _TOK, _URL2).is_file()
    captured = _install_urlopen(
        monkeypatch,
        [
            _http_304(),
            _http_304(),
        ],
    )
    a._request("GET", _URL2, etag=True)
    b._request("GET", _URL1, etag=True)
    assert _has_inm(captured[0])
    assert _has_inm(captured[1])


def test_prune_evicts_oldest_until_under_total_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "c"
    cache.mkdir()
    monkeypatch.setattr(gc_mod, "_ETAG_DISK_MAX_BYTES", 200)
    monkeypatch.setattr(gc_mod, "_ETAG_PRUNE_INTERVAL_SEC", 0.0)
    import os

    base = 1_000_000.0
    pad = "x" * 80
    for i in range(5):
        url = f"https://api.github.com/u/{i}"
        _write_entry(cache, _TOK, url, etag=f'"e{i}"', body={"i": i, "pad": pad})
        os.utime(_entry_path(cache, _TOK, url), (base + i, base + i))

    _install_urlopen(
        monkeypatch, [_FakeResp({"touched": 0}, _rate_headers("4999")), _http_304()]
    )
    client = GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)
    touch_url = "https://api.github.com/u/0"
    client._request("GET", touch_url, etag=True)
    client._request("GET", touch_url, etag=True)

    total = sum(p.stat().st_size for p in cache.iterdir() if p.is_file())
    assert total <= 200
    assert _entry_path(cache, _TOK, touch_url).is_file()
    assert not _entry_path(cache, _TOK, "https://api.github.com/u/4").is_file()


def test_oversized_entry_is_returned_but_not_stored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "c"
    monkeypatch.setattr(gc_mod, "_ETAG_ENTRY_MAX_BYTES", 80)
    small = {"ok": True}
    path = _entry_path(cache, _TOK, _URL1)
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"key": _disk_key(_TOK, _URL1), "etag": _ETAG, "body": small}),
        encoding="utf-8",
    )
    huge = {"blob": "x" * 500}
    _install_urlopen(monkeypatch, [_FakeResp(huge, _rate_headers("4999"))])
    client = GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)
    assert client._request("GET", _URL1, etag=True) == huge
    assert not path.is_file()


def test_legacy_cache_file_is_replaced_by_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "etag.json"
    cache.write_text(json.dumps({"legacy": True}), encoding="utf-8")
    payload = [{"fresh": True}]
    captured = _install_urlopen(
        monkeypatch, [_FakeResp(payload, _rate_headers("4999"))]
    )
    client = GitHubClient(token=_TOK, repo="owner/repo", etag_cache_path=cache)
    assert cache.is_dir()
    assert client._request("GET", _URL1, etag=True) == payload
    assert not _has_inm(captured[0])


def test_different_token_does_not_reuse_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "c"
    captured = _install_urlopen(
        monkeypatch,
        [
            _FakeResp([{"number": 1}], _rate_headers("4999")),
            _FakeResp([{"number": 1}], _rate_headers("4998")),
        ],
    )
    GitHubClient(token="tok-a", repo="owner/repo", etag_cache_path=cache)._request(
        "GET", _URL1, etag=True
    )
    GitHubClient(token="tok-b", repo="owner/repo", etag_cache_path=cache)._request(
        "GET", _URL1, etag=True
    )

    assert not _has_inm(captured[1])
    assert _entry_path(cache, "tok-a", _URL1).is_file()
    assert _entry_path(cache, "tok-b", _URL1).is_file()
