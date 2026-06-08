"""Tests for /api/correlation-bursts UI endpoint."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _ts(offset_sec: float = 0.0) -> str:
    dt = datetime.fromtimestamp(time.time() - offset_sec, tz=timezone(timedelta(hours=9)))
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
    def test_returns_bursts_json_shape(self, tmp_path):
        repo = _make_repo(tmp_path)
        audit_path = repo / "jobs" / "audit.jsonl"
        _write_audit(audit_path, [
            {"event_type": "task_complete", "uuid": f"u{i}", "status": "success",
             "correlation_id": "cid-a", "timestamp": _ts(30)}
            for i in range(3)
        ])

        server, port = _start_server(repo)
        try:
            data = _get_json(port, "/api/correlation-bursts?hours=1&top_n=10")
            assert "bursts" in data
            assert len(data["bursts"]) == 1
            entry = data["bursts"][0]
            assert entry["correlation_id"] == "cid-a"
            assert entry["count"] == 3
            assert isinstance(entry["latest_timestamp"], str)
        finally:
            server.shutdown()

    def test_hours_filters_to_recent_window(self, tmp_path):
        repo = _make_repo(tmp_path)
        audit_path = repo / "jobs" / "audit.jsonl"
        _write_audit(audit_path, [
            {"event_type": "task_complete", "uuid": "old", "status": "success",
             "correlation_id": "cid-old", "timestamp": _ts(7200)},
            {"event_type": "task_complete", "uuid": "new", "status": "success",
             "correlation_id": "cid-new", "timestamp": _ts(60)},
        ])

        server, port = _start_server(repo)
        try:
            data = _get_json(port, "/api/correlation-bursts?hours=1&top_n=10")
            assert len(data["bursts"]) == 1
            assert data["bursts"][0]["correlation_id"] == "cid-new"
        finally:
            server.shutdown()

    def test_top_n_limits_results(self, tmp_path):
        repo = _make_repo(tmp_path)
        audit_path = repo / "jobs" / "audit.jsonl"
        events = []
        for cid, count in [("cid-1", 5), ("cid-2", 20), ("cid-3", 10), ("cid-4", 15)]:
            for i in range(count):
                events.append({
                    "event_type": "task_complete",
                    "uuid": f"{cid}-u{i}",
                    "status": "success",
                    "correlation_id": cid,
                    "timestamp": _ts(10 + i),
                })
        _write_audit(audit_path, events)

        server, port = _start_server(repo)
        try:
            data = _get_json(port, "/api/correlation-bursts?hours=1&top_n=5")
            assert len(data["bursts"]) == 4
            ids = [b["correlation_id"] for b in data["bursts"]]
            assert ids == ["cid-2", "cid-4", "cid-3", "cid-1"]
        finally:
            server.shutdown()

    def test_default_query_params(self, tmp_path):
        repo = _make_repo(tmp_path)
        audit_path = repo / "jobs" / "audit.jsonl"
        events = []
        for i in range(25):
            cid = f"cid-{i}"
            for j in range(i + 1):
                events.append({
                    "event_type": "task_complete",
                    "uuid": f"{cid}-u{j}",
                    "status": "success",
                    "correlation_id": cid,
                    "timestamp": _ts(5 + j),
                })
        _write_audit(audit_path, events)

        server, port = _start_server(repo)
        try:
            data = _get_json(port, "/api/correlation-bursts")
            assert len(data["bursts"]) == 20
            counts = [b["count"] for b in data["bursts"]]
            assert counts == sorted(counts, reverse=True)
        finally:
            server.shutdown()

    def test_sorted_by_count_descending(self, tmp_path):
        repo = _make_repo(tmp_path)
        audit_path = repo / "jobs" / "audit.jsonl"
        _write_audit(audit_path, [
            {"event_type": "task_complete", "uuid": f"a{i}", "status": "success",
             "correlation_id": "low", "timestamp": _ts(20)}
            for i in range(2)
        ] + [
            {"event_type": "task_complete", "uuid": f"b{i}", "status": "success",
             "correlation_id": "high", "timestamp": _ts(15)}
            for i in range(7)
        ])

        server, port = _start_server(repo)
        try:
            data = _get_json(port, "/api/correlation-bursts?hours=1&top_n=10")
            assert [b["correlation_id"] for b in data["bursts"]] == ["high", "low"]
            assert data["bursts"][0]["count"] == 7
            assert data["bursts"][1]["count"] == 2
        finally:
            server.shutdown()

    def test_missing_audit_file_returns_empty(self, tmp_path):
        repo = _make_repo(tmp_path)

        server, port = _start_server(repo)
        try:
            data = _get_json(port, "/api/correlation-bursts?hours=1&top_n=10")
            assert data == {"bursts": []}
        finally:
            server.shutdown()

    def test_empty_audit_file_returns_empty(self, tmp_path):
        repo = _make_repo(tmp_path)
        (repo / "jobs" / "audit.jsonl").write_text("", encoding="utf-8")

        server, port = _start_server(repo)
        try:
            data = _get_json(port, "/api/correlation-bursts?hours=1&top_n=10")
            assert data == {"bursts": []}
        finally:
            server.shutdown()

    def test_excludes_none_correlation_id(self, tmp_path):
        repo = _make_repo(tmp_path)
        audit_path = repo / "jobs" / "audit.jsonl"
        _write_audit(audit_path, [
            {"event_type": "task_complete", "uuid": f"u{i}", "status": "success",
             "correlation_id": None, "timestamp": _ts(5)}
            for i in range(10)
        ])

        server, port = _start_server(repo)
        try:
            data = _get_json(port, "/api/correlation-bursts?hours=1&top_n=10")
            assert data == {"bursts": []}
        finally:
            server.shutdown()

    def test_no_events_in_window_returns_empty(self, tmp_path):
        repo = _make_repo(tmp_path)
        audit_path = repo / "jobs" / "audit.jsonl"
        _write_audit(audit_path, [
            {"event_type": "task_complete", "uuid": "old", "status": "success",
             "correlation_id": "cid-old", "timestamp": _ts(10000)},
        ])

        server, port = _start_server(repo)
        try:
            data = _get_json(port, "/api/correlation-bursts?hours=1&top_n=10")
            assert data == {"bursts": []}
        finally:
            server.shutdown()

    def test_existing_rows_endpoint_unaffected(self, tmp_path):
        repo = _make_repo(tmp_path)
        (repo / "jobs" / "exec.jsonl").write_text(
            json.dumps({"uuid": "aaaa-bbbb-cccc-0001", "command": "echo hello", "depends": []}) + "\n",
            encoding="utf-8",
        )

        server, port = _start_server(repo)
        try:
            data = _get_json(port, "/api/rows")
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["uuid"] == "aaaa-bbbb-cccc-0001"
        finally:
            server.shutdown()
