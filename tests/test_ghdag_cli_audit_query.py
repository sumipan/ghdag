"""Tests for ghdag audit-query subcommand."""

from __future__ import annotations

import json
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

FIXED_NOW = 1_762_600_000.0
_JST = timezone(timedelta(hours=9))


@pytest.fixture(autouse=True)
def _fixed_audit_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ghdag.io.audit_query.time",
        types.SimpleNamespace(time=lambda: FIXED_NOW),
    )


def _write_events(path: Path, events: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _ts(offset_sec: float = 0.0) -> str:
    dt = datetime.fromtimestamp(FIXED_NOW - offset_sec, tz=_JST)
    return dt.isoformat()


class TestAuditQueryLegacyEventKey:
    """Refs sumipan/ghdag#7552 — event_type filter matches legacy event rows."""

    def test_event_type_filter_reads_legacy_event_key(self, tmp_path):
        from ghdag.io.audit_query import read_task_exit_events

        audit = tmp_path / "audit.jsonl"
        _write_events(audit, [
            {
                "event": "task_complete",
                "uuid": "legacy",
                "status": "success",
                "correlation_id": "cid",
                "timestamp": "2026-06-07T10:00:00+09:00",
            },
            {
                "event_type": "task_complete",
                "uuid": "modern",
                "status": "success",
                "correlation_id": "cid",
                "timestamp": "2026-06-07T10:01:00+09:00",
            },
        ])

        result = read_task_exit_events(audit, event_type="task_complete")

        assert {row["uuid"] for row in result} == {"legacy", "modern"}

    def test_llm_audit_row_matches_event_type_filter(self, tmp_path):
        """AC2: a row written by write_llm_audit_log is found by event_type."""
        from ghdag.io.audit import write_llm_audit_log
        from ghdag.io.audit_query import read_task_exit_events

        audit = tmp_path / "audit.jsonl"
        write_llm_audit_log(audit, engine="claude", model="m", exit_code=0)

        result = read_task_exit_events(audit, event_type="llm_call")

        assert len(result) == 1
        assert result[0]["engine"] == "claude"

    def test_legacy_llm_call_row_matches_event_type_filter(self, tmp_path):
        """AC4: a legacy {"event": "llm_call"} row is found by event_type."""
        from ghdag.io.audit_query import read_task_exit_events

        audit = tmp_path / "audit.jsonl"
        _write_events(audit, [
            {"event": "llm_call", "engine": "claude", "model": "m",
             "timestamp": "2026-06-07T10:00:00+09:00"},
        ])

        result = read_task_exit_events(audit, event_type="llm_call")

        assert len(result) == 1
        assert result[0]["event"] == "llm_call"


class TestAuditQueryCorrelationId:
    def test_since_filter(self, tmp_path, capsys):
        from ghdag.cli import main

        audit = tmp_path / "audit.jsonl"
        cid = "research:pipeline:1750"
        _write_events(audit, [
            {"event_type": "task_complete", "uuid": "old", "status": "success",
             "correlation_id": cid, "timestamp": "2026-06-07T10:00:00+09:00"},
            {"event_type": "task_complete", "uuid": "new", "status": "success",
             "correlation_id": cid, "timestamp": "2026-06-09T10:00:00+09:00"},
        ])

        main([
            "audit-query", "--correlation-id", cid,
            "--since", "2026-06-08", "--audit-path", str(audit),
        ])

        captured = capsys.readouterr()
        lines = [line for line in captured.out.strip().split("\n") if line]
        assert len(lines) == 1
        assert json.loads(lines[0])["uuid"] == "new"


class TestAuditQueryBurstDetect:
    def test_burst_found_exit_1(self, tmp_path, capsys):
        from ghdag.cli import main

        audit = tmp_path / "audit.jsonl"
        cid = "issuesmith:B1:burst"
        _write_events(audit, [
            {"event_type": "task_complete", "uuid": f"u{i}", "status": "success",
             "correlation_id": cid, "timestamp": _ts(30)}
            for i in range(12)
        ])

        with pytest.raises(SystemExit) as exc:
            main(["audit-query", "--burst-detect", "--audit-path", str(audit)])

        assert exc.value.code == 1
        captured = capsys.readouterr()
        result = json.loads(captured.out.strip())
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["correlation_id"] == cid
        assert result[0]["count"] == 12
        assert result[0]["latest_timestamp"]


class TestAuditQueryErrors:
    def test_neither_mode_exit_2(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["audit-query"])

        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert captured.err

    def test_both_modes_exit_2(self, tmp_path, capsys):
        from ghdag.cli import main

        audit = tmp_path / "audit.jsonl"
        audit.write_text("")

        with pytest.raises(SystemExit) as exc:
            main([
                "audit-query",
                "--correlation-id", "cid",
                "--burst-detect",
                "--audit-path", str(audit),
            ])

        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert captured.err

    def test_invalid_since_exit_2(self, tmp_path, capsys):
        from ghdag.cli import main

        audit = tmp_path / "audit.jsonl"
        audit.write_text("")

        with pytest.raises(SystemExit) as exc:
            main([
                "audit-query",
                "--correlation-id", "cid",
                "--since", "not-a-datetime",
                "--audit-path", str(audit),
            ])

        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert captured.err


class TestAuditQueryMissingFile:
    def test_missing_audit_path_empty_output_exit_0(self, tmp_path, capsys):
        from ghdag.cli import main

        missing = tmp_path / "nonexistent.jsonl"

        main(["audit-query", "--correlation-id", "cid", "--audit-path", str(missing)])

        captured = capsys.readouterr()
        assert captured.out == ""

        capsys.readouterr()
        main(["audit-query", "--burst-detect", "--audit-path", str(missing)])
        captured = capsys.readouterr()
        assert captured.out.strip() == "[]"
