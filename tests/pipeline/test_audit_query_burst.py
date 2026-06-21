"""Tests for correlation burst detection helpers in pipeline/audit_query.py."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from ghdag.pipeline.audit_query import detect_correlation_bursts, get_correlation_top_n


def _write_events(path: Path, events: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _ts(offset_sec: float = 0.0) -> str:
    dt = datetime.fromtimestamp(time.time() - offset_sec, tz=timezone(timedelta(hours=9)))
    return dt.isoformat()


@pytest.fixture
def audit_path(tmp_path):
    return tmp_path / "audit.jsonl"


class TestDetectCorrelationBursts:
    def test_detects_burst_above_threshold(self, audit_path):
        cid = "issuesmith:B1:123"
        _write_events(audit_path, [
            {"event_type": "task_complete", "uuid": f"u{i}", "status": "success",
             "correlation_id": cid, "timestamp": _ts(60)}
            for i in range(15)
        ])

        result = detect_correlation_bursts(audit_path, window_sec=600, threshold=10)

        assert len(result) == 1
        assert result[0]["correlation_id"] == cid
        assert result[0]["count"] == 15
        assert result[0]["latest_timestamp"] is not None

    def test_detects_burst_at_exact_threshold(self, audit_path):
        cid = "issuesmith:B1:456"
        _write_events(audit_path, [
            {"event_type": "task_complete", "uuid": f"u{i}", "status": "success",
             "correlation_id": cid, "timestamp": _ts(30)}
            for i in range(10)
        ])

        result = detect_correlation_bursts(audit_path, window_sec=600, threshold=10)

        assert len(result) == 1
        assert result[0]["count"] == 10

    def test_excludes_below_threshold(self, audit_path):
        cid = "issuesmith:B1:789"
        _write_events(audit_path, [
            {"event_type": "task_complete", "uuid": f"u{i}", "status": "success",
             "correlation_id": cid, "timestamp": _ts(10)}
            for i in range(9)
        ])

        result = detect_correlation_bursts(audit_path, window_sec=600, threshold=10)

        assert result == []

    def test_missing_file_returns_empty(self, tmp_path):
        result = detect_correlation_bursts(tmp_path / "nonexistent.jsonl")
        assert result == []

    def test_excludes_none_correlation_id(self, audit_path):
        _write_events(audit_path, [
            {"event_type": "task_complete", "uuid": f"u{i}", "status": "success",
             "correlation_id": None, "timestamp": _ts(5)}
            for i in range(20)
        ])

        result = detect_correlation_bursts(audit_path, window_sec=600, threshold=10)

        assert result == []

    def test_multiple_correlation_ids_sorted_by_count(self, audit_path):
        _write_events(audit_path, [
            {"event_type": "task_complete", "uuid": f"a{i}", "status": "success",
             "correlation_id": "cid-a", "timestamp": _ts(20)}
            for i in range(12)
        ] + [
            {"event_type": "task_complete", "uuid": f"b{i}", "status": "success",
             "correlation_id": "cid-b", "timestamp": _ts(15)}
            for i in range(15)
        ])

        result = detect_correlation_bursts(audit_path, window_sec=600, threshold=10)

        assert len(result) == 2
        assert result[0]["correlation_id"] == "cid-b"
        assert result[0]["count"] == 15
        assert result[1]["correlation_id"] == "cid-a"
        assert result[1]["count"] == 12

    def test_excludes_events_outside_window(self, audit_path):
        cid = "issuesmith:B1:old"
        _write_events(audit_path, [
            {"event_type": "task_complete", "uuid": f"u{i}", "status": "success",
             "correlation_id": cid, "timestamp": _ts(700)}
            for i in range(15)
        ])

        result = detect_correlation_bursts(audit_path, window_sec=600, threshold=10)

        assert result == []


class TestGetCorrelationTopN:
    def test_returns_top_n_sorted_by_count(self, audit_path):
        events = []
        for rank, (cid, count) in enumerate([
            ("cid-1", 5), ("cid-2", 20), ("cid-3", 10),
            ("cid-4", 15), ("cid-5", 3), ("cid-6", 8),
        ]):
            for i in range(count):
                events.append({
                    "event_type": "task_complete",
                    "uuid": f"{cid}-u{i}",
                    "status": "success",
                    "correlation_id": cid,
                    "timestamp": _ts(rank * 10 + i),
                })
        _write_events(audit_path, events)

        result = get_correlation_top_n(audit_path, since_sec=3600, top_n=5)

        assert len(result) == 5
        assert [r["correlation_id"] for r in result] == [
            "cid-2", "cid-4", "cid-3", "cid-6", "cid-1",
        ]
        assert result[0]["count"] == 20

    def test_excludes_none_correlation_id(self, audit_path):
        _write_events(audit_path, [
            {"event_type": "task_complete", "uuid": f"u{i}", "status": "success",
             "correlation_id": None, "timestamp": _ts(5)}
            for i in range(10)
        ])

        result = get_correlation_top_n(audit_path, since_sec=3600, top_n=5)

        assert result == []

    def test_missing_file_returns_empty(self, tmp_path):
        result = get_correlation_top_n(tmp_path / "nonexistent.jsonl", since_sec=3600)
        assert result == []

    def test_uses_since_sec_window(self, audit_path):
        _write_events(audit_path, [
            {"event_type": "task_complete", "uuid": "old", "status": "success",
             "correlation_id": "cid-old", "timestamp": _ts(7200)},
            {"event_type": "task_complete", "uuid": "new", "status": "success",
             "correlation_id": "cid-new", "timestamp": _ts(60)},
        ])

        with patch("ghdag.pipeline.audit_query.time.time", return_value=time.time()):
            result = get_correlation_top_n(audit_path, since_sec=3600, top_n=5)

        assert len(result) == 1
        assert result[0]["correlation_id"] == "cid-new"
