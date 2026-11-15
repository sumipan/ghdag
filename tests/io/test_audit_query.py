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
