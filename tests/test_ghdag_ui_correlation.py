"""Tests for /api/correlation-bursts UI endpoint."""

from __future__ import annotations

import json
import threading
import types
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

FIXED_NOW = 1_762_600_000.0
_JST = timezone(timedelta(hours=9))


@pytest.fixture(autouse=True)
def _fixed_audit_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.SimpleNamespace(time=lambda: FIXED_NOW)
    monkeypatch.setattr("ghdag.io.audit_query.time", fake)
    monkeypatch.delenv("GHDAG_AUDIT_PATH", raising=False)


def _ts(offset_sec: float = 0.0) -> str:
    dt = datetime.fromtimestamp(FIXED_NOW - offset_sec, tz=_JST)
    return dt.isoformat()


def _write_audit(path: Path, events: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _start_server(repo: Path):
    from http.server import HTTPServer

    from ghdag.ui.server import _Handler

    _Handler.repo_root = repo
    _Handler.poll_interval = 1.0
    _Handler.max_visible = 30
    _Handler.github_base_url = None

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, port


def _get_json(port: int, path: str) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    resp = urllib.request.urlopen(url, timeout=5)
    return json.loads(resp.read().decode("utf-8"))


def _make_repo(tmp_path: Path) -> Path:
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    (jobs_dir / "exec.jsonl").write_text("", encoding="utf-8")
    (jobs_dir / "done").mkdir(parents=True, exist_ok=True)
    return tmp_path


class TestCorrelationBurstsEndpoint:


    def test_missing_audit_file_returns_empty(self, tmp_path):
        repo = _make_repo(tmp_path)

        server, port = _start_server(repo)
        try:
            data = _get_json(port, "/api/correlation-bursts?hours=1&top_n=10")
            assert data == {"bursts": []}
        finally:
            server.shutdown()


