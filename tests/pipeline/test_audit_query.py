"""Tests for pipeline/audit_query.py (Issue #983 A1-1, Issue #1046 AC-8–12)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghdag.pipeline import get_latest_status as public_get
from ghdag.pipeline import read_task_exit_events as public_read
from ghdag.pipeline.audit_query import get_latest_status, read_task_exit_events


def _write_events(path: Path, events: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


@pytest.fixture
def audit_path(tmp_path):
    return tmp_path / "audit.jsonl"


# ---------------------------------------------------------------------------
# read_task_exit_events
# ---------------------------------------------------------------------------

class TestReadTaskExitEvents:
    def test_filter_by_correlation_id(self, audit_path):
        _write_events(audit_path, [
            {"event_type": "task_complete", "uuid": "u1", "status": "success",
             "correlation_id": "issuesmith:B1:123", "timestamp": "2026-01-01T00:00:00+09:00"},
            {"event_type": "task_failed", "uuid": "u2", "status": "failure",
             "correlation_id": "issuesmith:B1:999", "timestamp": "2026-01-01T00:01:00+09:00"},
        ])

        result = read_task_exit_events(audit_path, correlation_id="issuesmith:B1:123")

        assert len(result) == 1
        assert result[0]["uuid"] == "u1"

    def test_filter_by_uuid(self, audit_path):
        _write_events(audit_path, [
            {"event_type": "task_complete", "uuid": "abc-def", "status": "success",
             "correlation_id": "cid1", "timestamp": "2026-01-01T00:00:00+09:00"},
            {"event_type": "task_complete", "uuid": "xyz-000", "status": "success",
             "correlation_id": "cid2", "timestamp": "2026-01-01T00:01:00+09:00"},
        ])

        result = read_task_exit_events(audit_path, uuid="abc-def")

        assert len(result) == 1
        assert result[0]["uuid"] == "abc-def"

    def test_filter_by_event_type(self, audit_path):
        _write_events(audit_path, [
            {"event_type": "task_failed", "uuid": "u1", "status": "failure",
             "correlation_id": "cid1", "timestamp": "2026-01-01T00:00:00+09:00"},
            {"event_type": "task_complete", "uuid": "u2", "status": "success",
             "correlation_id": "cid1", "timestamp": "2026-01-01T00:01:00+09:00"},
            {"event_type": "task_failed", "uuid": "u3", "status": "failure",
             "correlation_id": "cid2", "timestamp": "2026-01-01T00:02:00+09:00"},
        ])

        result = read_task_exit_events(audit_path, event_type="task_failed")

        assert len(result) == 2
        assert all(r["event_type"] == "task_failed" for r in result)

    def test_limit_returns_latest_n(self, audit_path):
        events = [
            {"event_type": "task_failed", "uuid": f"u{i}", "status": "failure",
             "correlation_id": "cid", "timestamp": f"2026-01-0{i+1}T00:00:00+09:00"}
            for i in range(5)
        ]
        _write_events(audit_path, events)

        result = read_task_exit_events(audit_path, event_type="task_failed", limit=3)

        assert len(result) == 3
        assert result[0]["uuid"] == "u2"
        assert result[1]["uuid"] == "u3"
        assert result[2]["uuid"] == "u4"

    def test_filter_by_since(self, audit_path):
        _write_events(audit_path, [
            {"event_type": "task_complete", "uuid": "u1", "status": "success",
             "correlation_id": "cid", "timestamp": "2026-01-01T00:00:00+09:00"},
            {"event_type": "task_complete", "uuid": "u2", "status": "success",
             "correlation_id": "cid", "timestamp": "2026-06-01T00:00:00+09:00"},
        ])
        # epoch for 2026-03-01T00:00:00+09:00
        import datetime
        since_dt = datetime.datetime(2026, 3, 1, 0, 0, 0,
                                     tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
        since_epoch = since_dt.timestamp()

        result = read_task_exit_events(audit_path, since=since_epoch)

        assert len(result) == 1
        assert result[0]["uuid"] == "u2"

    def test_and_filter_correlation_and_event_type(self, audit_path):
        _write_events(audit_path, [
            {"event_type": "task_failed", "uuid": "u1", "status": "failure",
             "correlation_id": "X", "timestamp": "2026-01-01T00:00:00+09:00"},
            {"event_type": "task_complete", "uuid": "u2", "status": "success",
             "correlation_id": "X", "timestamp": "2026-01-01T00:01:00+09:00"},
            {"event_type": "task_failed", "uuid": "u3", "status": "failure",
             "correlation_id": "Y", "timestamp": "2026-01-01T00:02:00+09:00"},
        ])

        result = read_task_exit_events(audit_path, correlation_id="X", event_type="task_failed")

        assert len(result) == 1
        assert result[0]["uuid"] == "u1"

    def test_file_not_exists_returns_empty(self, tmp_path):
        result = read_task_exit_events(tmp_path / "nonexistent.jsonl")
        assert result == []

    def test_skips_invalid_json_lines(self, audit_path):
        audit_path.write_text(
            '{"event_type": "task_complete", "uuid": "u1", "status": "success", '
            '"correlation_id": "cid", "timestamp": "2026-01-01T00:00:00+09:00"}\n'
            "this-is-not-json\n"
            '{"event_type": "task_complete", "uuid": "u2", "status": "success", '
            '"correlation_id": "cid", "timestamp": "2026-01-01T00:01:00+09:00"}\n',
            encoding="utf-8",
        )

        result = read_task_exit_events(audit_path)

        assert len(result) == 2
        uuids = {r["uuid"] for r in result}
        assert uuids == {"u1", "u2"}

    def test_no_filters_returns_all(self, audit_path):
        _write_events(audit_path, [
            {"event_type": "task_complete", "uuid": "u1", "status": "success",
             "correlation_id": "c1", "timestamp": "2026-01-01T00:00:00+09:00"},
            {"event_type": "task_failed", "uuid": "u2", "status": "failure",
             "correlation_id": "c2", "timestamp": "2026-01-01T00:01:00+09:00"},
        ])

        result = read_task_exit_events(audit_path)

        assert len(result) == 2


# ---------------------------------------------------------------------------
# get_latest_status
# ---------------------------------------------------------------------------

class TestGetLatestStatus:
    def test_returns_latest_status(self, audit_path):
        _write_events(audit_path, [
            {"event_type": "task_failed", "uuid": "u1", "status": "failure",
             "correlation_id": "issuesmith:B1:123", "timestamp": "2026-01-01T00:00:00+09:00"},
            {"event_type": "task_complete", "uuid": "u2", "status": "success",
             "correlation_id": "issuesmith:B1:123", "timestamp": "2026-01-01T00:01:00+09:00"},
        ])

        result = get_latest_status(audit_path, "issuesmith:B1:123")

        assert result == "success"

    def test_returns_none_for_unknown_id(self, audit_path):
        _write_events(audit_path, [
            {"event_type": "task_complete", "uuid": "u1", "status": "success",
             "correlation_id": "issuesmith:B1:123", "timestamp": "2026-01-01T00:00:00+09:00"},
        ])

        result = get_latest_status(audit_path, "存在しないID")

        assert result is None

    def test_returns_none_when_file_missing(self, tmp_path):
        result = get_latest_status(tmp_path / "nonexistent.jsonl", "any:id")
        assert result is None

    def test_returns_none_for_empty_file(self, audit_path):
        audit_path.write_text("", encoding="utf-8")
        result = get_latest_status(audit_path, "any:id")
        assert result is None


# ---------------------------------------------------------------------------
# Public API: __init__.py re-exports
# ---------------------------------------------------------------------------

class TestPublicAPI:
    def test_read_task_exit_events_importable(self, audit_path):
        _write_events(audit_path, [
            {"event_type": "task_complete", "uuid": "u1", "status": "success",
             "correlation_id": "cid", "timestamp": "2026-01-01T00:00:00+09:00"},
        ])
        result = public_read(audit_path, correlation_id="cid")
        assert len(result) == 1

    def test_get_latest_status_importable(self, audit_path):
        _write_events(audit_path, [
            {"event_type": "task_complete", "uuid": "u1", "status": "success",
             "correlation_id": "cid", "timestamp": "2026-01-01T00:00:00+09:00"},
        ])
        result = public_get(audit_path, "cid")
        assert result == "success"


# ---------------------------------------------------------------------------
# Multi-file read (Issue #1046) — AC-8 through AC-12
# ---------------------------------------------------------------------------

class TestMultiFileRead:
    def test_ac8_rotated_plus_current_returns_all(self, tmp_path):
        """AC-8: 3 records in rotated + 2 in current → 5 total."""
        audit_path = tmp_path / "audit.jsonl"
        rotated = tmp_path / "audit.2026-05-22T00-00-00.jsonl"

        _write_events(rotated, [
            {"event_type": "task_complete", "uuid": f"u{i}", "status": "success",
             "correlation_id": "cid", "timestamp": "2026-05-22T00:00:00+09:00"}
            for i in range(3)
        ])
        _write_events(audit_path, [
            {"event_type": "task_complete", "uuid": f"u{i+3}", "status": "success",
             "correlation_id": "cid", "timestamp": "2026-05-23T00:00:00+09:00"}
            for i in range(2)
        ])

        result = read_task_exit_events(audit_path)
        assert len(result) == 5

    def test_ac9_rotated_only_no_current(self, tmp_path):
        """AC-9: only rotated file exists → records returned."""
        audit_path = tmp_path / "audit.jsonl"
        rotated = tmp_path / "audit.2026-05-22T00-00-00.jsonl"

        _write_events(rotated, [
            {"event_type": "task_complete", "uuid": "u1", "status": "success",
             "correlation_id": "cid", "timestamp": "2026-05-22T00:00:00+09:00"},
        ])

        result = read_task_exit_events(audit_path)
        assert len(result) == 1
        assert result[0]["uuid"] == "u1"

    def test_ac10_since_filter_across_files(self, tmp_path):
        """AC-10: since filter applies across rotated + current."""
        import datetime
        audit_path = tmp_path / "audit.jsonl"
        rotated = tmp_path / "audit.2026-05-22T00-00-00.jsonl"

        _write_events(rotated, [
            {"event_type": "task_complete", "uuid": "old", "status": "success",
             "correlation_id": "cid", "timestamp": "2026-05-22T00:00:00+09:00"},
        ])
        _write_events(audit_path, [
            {"event_type": "task_complete", "uuid": "new", "status": "success",
             "correlation_id": "cid", "timestamp": "2026-05-23T00:00:00+09:00"},
        ])

        since_dt = datetime.datetime(
            2026, 5, 23, 0, 0, 0,
            tzinfo=datetime.timezone(datetime.timedelta(hours=9)),
        )
        result = read_task_exit_events(audit_path, since=since_dt.timestamp())

        assert len(result) == 1
        assert result[0]["uuid"] == "new"

    def test_ac11_get_latest_status_from_rotated(self, tmp_path):
        """AC-11: get_latest_status finds record in rotated file."""
        audit_path = tmp_path / "audit.jsonl"
        rotated = tmp_path / "audit.2026-05-22T00-00-00.jsonl"

        _write_events(rotated, [
            {"event_type": "task_complete", "uuid": "u1", "status": "success",
             "correlation_id": "cid:123", "timestamp": "2026-05-22T00:00:00+09:00"},
        ])

        result = get_latest_status(audit_path, "cid:123")
        assert result == "success"

    def test_ac12_no_files_returns_empty(self, tmp_path):
        """AC-12: no files at all → empty list."""
        result = read_task_exit_events(tmp_path / "audit.jsonl")
        assert result == []
