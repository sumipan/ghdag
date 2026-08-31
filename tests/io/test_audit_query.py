"""Tests for ghdag.io.audit_query — read API move (nexus Issue #2673)."""

from __future__ import annotations

import inspect
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
    def test_pipeline_shim_same_objects(self) -> None:
        import ghdag.io.audit_query as io_aq
        import ghdag.pipeline.audit_query as pipeline_aq

        assert pipeline_aq.read_task_exit_events is io_aq.read_task_exit_events
        assert pipeline_aq.get_latest_status is io_aq.get_latest_status
        assert pipeline_aq.detect_correlation_bursts is io_aq.detect_correlation_bursts
        assert pipeline_aq.get_correlation_top_n is io_aq.get_correlation_top_n

    def test_pipeline_package_reexports_from_io(self) -> None:
        from ghdag.io.audit_query import get_latest_status, read_task_exit_events
        from ghdag.pipeline import get_latest_status as pub_status
        from ghdag.pipeline import read_task_exit_events as pub_read

        assert pub_read is read_task_exit_events
        assert pub_status is get_latest_status

    def test_canonical_source_file(self) -> None:
        from ghdag.io.audit_query import read_task_exit_events

        src = inspect.getsourcefile(read_task_exit_events)
        assert src is not None
        assert "io/audit_query.py" in src.replace("\\", "/")
