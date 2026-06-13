"""Tests for ghdag audit-query subcommand."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


def _write_events(path: Path, events: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _ts(offset_sec: float = 0.0) -> str:
    dt = datetime.fromtimestamp(time.time() - offset_sec, tz=timezone(timedelta(hours=9)))
    return dt.isoformat()


class TestAuditQueryCorrelationId:
    def test_filters_by_correlation_id_json_lines(self, tmp_path, capsys):
        from ghdag.cli import main

        audit = tmp_path / "audit.jsonl"
        cid = "research:pipeline:1750"
        _write_events(audit, [
            {"event_type": "task_complete", "uuid": "u1", "status": "success",
             "correlation_id": cid, "timestamp": "2026-06-08T10:00:00+09:00"},
            {"event_type": "task_complete", "uuid": "u2", "status": "success",
             "correlation_id": "other", "timestamp": "2026-06-08T11:00:00+09:00"},
        ])

        main(["audit-query", "--correlation-id", cid, "--audit-path", str(audit)])

        captured = capsys.readouterr()
        lines = [line for line in captured.out.strip().split("\n") if line]
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["correlation_id"] == cid
        assert rec["uuid"] == "u1"

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

    def test_empty_result_no_output(self, tmp_path, capsys):
        from ghdag.cli import main

        audit = tmp_path / "audit.jsonl"
        _write_events(audit, [
            {"event_type": "task_complete", "uuid": "u1", "status": "success",
             "correlation_id": "other", "timestamp": "2026-06-08T10:00:00+09:00"},
        ])

        main(["audit-query", "--correlation-id", "missing", "--audit-path", str(audit)])

        captured = capsys.readouterr()
        assert captured.out == ""


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

    def test_no_burst_exit_0_empty_array(self, tmp_path, capsys):
        from ghdag.cli import main

        audit = tmp_path / "audit.jsonl"
        _write_events(audit, [
            {"event_type": "task_complete", "uuid": f"u{i}", "status": "success",
             "correlation_id": "cid", "timestamp": _ts(10)}
            for i in range(3)
        ])

        main(["audit-query", "--burst-detect", "--audit-path", str(audit)])

        captured = capsys.readouterr()
        assert captured.out.strip() == "[]"

    def test_custom_window_and_threshold(self, tmp_path, capsys):
        from ghdag.cli import main

        audit = tmp_path / "audit.jsonl"
        with patch(
            "ghdag.pipeline.audit_query.detect_correlation_bursts",
            return_value=[],
        ) as mock_detect:
            main([
                "audit-query", "--burst-detect",
                "--window-sec", "300", "--threshold", "5",
                "--audit-path", str(audit),
            ])

        mock_detect.assert_called_once_with(
            audit, window_sec=300.0, threshold=5,
        )


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
