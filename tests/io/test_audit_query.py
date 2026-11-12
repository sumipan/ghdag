"""Tests for ghdag.io.audit_query — read API move (nexus Issue #2673)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_events(path: Path, events: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


class TestIoAuditQuery:
    def test_read_task_exit_events(self, audit_path: Path) -> None:
        from ghdag.io.audit_query import read_task_exit_events

        _write_events(
            audit_path,
            [
                {
                    "event_type": "task_complete",
                    "uuid": "u1",
                    "status": "success",
                    "correlation_id": "c1",
                    "timestamp": "2026-01-01T00:00:00+09:00",
                },
                {
                    "event_type": "task_failed",
                    "uuid": "u2",
                    "status": "failure",
                    "correlation_id": "c2",
                    "timestamp": "2026-01-01T00:01:00+09:00",
                },
            ],
        )

        result = read_task_exit_events(audit_path, correlation_id="c1")
        assert len(result) == 1
        assert result[0]["uuid"] == "u1"

    def test_get_latest_status(self, audit_path: Path) -> None:
        from ghdag.io.audit_query import get_latest_status

        _write_events(
            audit_path,
            [
                {
                    "event_type": "task_complete",
                    "uuid": "u1",
                    "status": "running",
                    "correlation_id": "c1",
                    "timestamp": "2026-01-01T00:00:00+09:00",
                },
                {
                    "event_type": "task_complete",
                    "uuid": "u1",
                    "status": "success",
                    "correlation_id": "c1",
                    "timestamp": "2026-01-01T00:01:00+09:00",
                },
            ],
        )
        assert get_latest_status(audit_path, "c1") == "success"

    def test_detect_and_top_n_resolvable(self) -> None:
        from ghdag.io.audit_query import detect_correlation_bursts, get_correlation_top_n

        assert callable(detect_correlation_bursts)
        assert callable(get_correlation_top_n)


class TestCompatAuditQuery:
    def test_pipeline_package_reexports_from_io(self) -> None:
        import ghdag.pipeline as pipeline_pkg
        from ghdag.io.audit_query import get_latest_status, read_task_exit_events

        assert "read_task_exit_events" not in pipeline_pkg.__all__
        assert "get_latest_status" not in pipeline_pkg.__all__
        assert not hasattr(pipeline_pkg, "read_task_exit_events")
        assert not hasattr(pipeline_pkg, "get_latest_status")
        assert callable(read_task_exit_events)
        assert callable(get_latest_status)
