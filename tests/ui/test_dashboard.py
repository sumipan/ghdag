"""Tests for ghdag.ui.dashboard — audit.jsonl aggregation for the Web UI."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

JST = timezone(timedelta(hours=9))


def _write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _ts(offset_hours: float = 0) -> str:
    base = datetime(2026, 6, 13, 12, 0, 0, tzinfo=JST)
    return (base + timedelta(hours=offset_hours)).isoformat()


@pytest.fixture
def audit_path(tmp_path):
    return tmp_path / "jobs" / "audit.jsonl"


@pytest.fixture
def fixed_now():
    return datetime(2026, 6, 13, 12, 0, 0, tzinfo=JST).timestamp()


class TestAggregateTaskStatus:
    def test_aggregates_by_status_and_failure_class(self, audit_path, fixed_now):
        from ghdag.ui.dashboard import aggregate_task_status

        _write_events(audit_path, [
            {"event_type": "task_complete", "status": "success", "failure_class": None,
             "timestamp": _ts(-1), "correlation_id": "c1", "uuid": "u1"},
            {"event_type": "task_failed", "status": "failure", "failure_class": "TIMEOUT",
             "timestamp": _ts(-2), "correlation_id": "c1", "uuid": "u2"},
            {"event_type": "task_rejected", "status": "rejected", "failure_class": None,
             "timestamp": _ts(-3), "correlation_id": "c2", "uuid": "u3"},
            {"event_type": "llm_call", "status": "success", "failure_class": None,
             "timestamp": _ts(-1), "correlation_id": "c3", "uuid": "u4"},
        ])

        with patch("ghdag.ui.dashboard.time.time", return_value=fixed_now):
            result = aggregate_task_status(audit_path, since_sec=86400.0)

        assert result["total"] == 3
        assert result["by_status"]["success"] == 1
        assert result["by_status"]["failure"] == 1
        assert result["by_status"]["rejected"] == 1
        assert result["by_failure_class"]["TIMEOUT"] == 1
        assert "period_start" in result
        assert "period_end" in result

class TestAggregateTokenUsage:
    def test_null_token_count_excluded(self, audit_path, fixed_now):
        from ghdag.ui.dashboard import aggregate_token_usage

        _write_events(audit_path, [
            {"event_type": "task_complete", "status": "success", "token_count": 100,
             "correlation_id": "c1", "timestamp": _ts(-1), "uuid": "u1"},
            {"event_type": "task_complete", "status": "success", "token_count": None,
             "correlation_id": "c1", "timestamp": _ts(-2), "uuid": "u2"},
        ])

        with patch("ghdag.ui.dashboard.time.time", return_value=fixed_now):
            result = aggregate_token_usage(audit_path, since_sec=86400.0)

        assert result["grand_total_tokens"] == 100
        assert result["by_correlation"][0]["total_tokens"] == 100
        assert result["by_correlation"][0]["task_count"] == 1

    def test_warn_threshold_from_env(self, audit_path, fixed_now, monkeypatch):
        from ghdag.ui.dashboard import aggregate_token_usage

        monkeypatch.setenv("GHDAG_TOKEN_WARN_THRESHOLD", "1000")
        _write_events(audit_path, [
            {"event_type": "task_complete", "status": "success", "token_count": 500,
             "correlation_id": "c1", "timestamp": _ts(-1), "uuid": "u1"},
        ])

        with patch("ghdag.ui.dashboard.time.time", return_value=fixed_now):
            result = aggregate_token_usage(audit_path, since_sec=86400.0)

        assert result["warn_threshold"] == 1000
        assert result["by_correlation"][0]["over_threshold"] is False


class TestDashboardApiEndpoints:
    @pytest.fixture(autouse=True)
    def _pin_audit_path(self, monkeypatch, tmp_path):
        # Endpoints resolve the audit path from GHDAG_AUDIT_PATH; pin it to the
        # audit file each test writes so the runner environment cannot leak in.
        monkeypatch.setenv("GHDAG_AUDIT_PATH", str(tmp_path / "jobs" / "audit.jsonl"))

    def _start_server(self, repo_root: Path):
        import urllib.request
        from http.server import HTTPServer

        from ghdag.ui.server import _Handler

        _Handler.repo_root = repo_root
        _Handler.poll_interval = 1.0
        _Handler.max_visible = 30
        _Handler.github_base_url = None

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return server, port, urllib.request

    def test_dashboard_status_endpoint(self, tmp_path, fixed_now):
        audit_path = tmp_path / "jobs" / "audit.jsonl"
        _write_events(audit_path, [
            {"event_type": "task_complete", "status": "success", "failure_class": None,
             "timestamp": _ts(-1), "correlation_id": "c1", "uuid": "u1"},
        ])
        (tmp_path / "jobs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "jobs" / "exec.jsonl").write_text("", encoding="utf-8")

        server, port, urllib_request = self._start_server(tmp_path)
        try:
            with patch("ghdag.ui.dashboard.time.time", return_value=fixed_now):
                resp = urllib_request.urlopen(
                    f"http://127.0.0.1:{port}/api/dashboard/status", timeout=5,
                )
                data = json.loads(resp.read().decode("utf-8"))
            assert data["total"] == 1
            assert data["by_status"]["success"] == 1
        finally:
            server.shutdown()

    def test_dashboard_tokens_endpoint(self, tmp_path, fixed_now):
        audit_path = tmp_path / "jobs" / "audit.jsonl"
        _write_events(audit_path, [
            {"event_type": "task_complete", "status": "success", "token_count": 1000,
             "correlation_id": "c1", "timestamp": _ts(-1), "uuid": "u1"},
        ])
        (tmp_path / "jobs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "jobs" / "exec.jsonl").write_text("", encoding="utf-8")

        server, port, urllib_request = self._start_server(tmp_path)
        try:
            with patch("ghdag.ui.dashboard.time.time", return_value=fixed_now):
                resp = urllib_request.urlopen(
                    f"http://127.0.0.1:{port}/api/dashboard/tokens?threshold=500", timeout=5,
                )
                data = json.loads(resp.read().decode("utf-8"))
            assert data["grand_total_tokens"] == 1000
            assert data["warn_threshold"] == 500
        finally:
            server.shutdown()

    def test_dashboard_cb_firing_endpoint(self, tmp_path, fixed_now):
        audit_path = tmp_path / "jobs" / "audit.jsonl"
        _write_events(audit_path, [
            {"event_type": "task_failed", "status": "failure", "failure_class": "TIMEOUT",
             "timestamp": _ts(-1), "correlation_id": "c1", "uuid": "u1"},
        ])
        (tmp_path / "jobs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "jobs" / "exec.jsonl").write_text("", encoding="utf-8")

        server, port, urllib_request = self._start_server(tmp_path)
        try:
            with patch("ghdag.ui.dashboard.time.time", return_value=fixed_now):
                resp = urllib_request.urlopen(
                    f"http://127.0.0.1:{port}/api/dashboard/cb-firing?window=30", timeout=5,
                )
                data = json.loads(resp.read().decode("utf-8"))
            assert data["total_failures"] == 1
        finally:
            server.shutdown()

