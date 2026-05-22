"""Tests for write_task_exit_audit() — Issue #960 AC-1, AC-2, ..., AC-9."""

from __future__ import annotations

import json

import pytest

from ghdag.pipeline.audit import write_task_exit_audit


UUID = "8cad156e-0000-0000-0000-000000000001"


class TestWriteTaskExitAudit:
    def test_ac1_task_complete(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        write_task_exit_audit(
            audit_path,
            event_type="task_complete",
            uuid=UUID,
            status="success",
            elapsed_sec=10.5,
            token_count=1500,
            model="claude-sonnet-4-6",
            engine="claude",
        )

        assert audit_path.exists()
        records = [json.loads(line) for line in audit_path.read_text().splitlines()]
        assert len(records) == 1
        r = records[0]
        assert r["schema_version"] == 1
        assert r["event_type"] == "task_complete"
        assert r["uuid"] == UUID
        assert r["status"] == "success"
        assert r["elapsed_sec"] == 10.5
        assert r["token_count"] == 1500
        assert r["model"] == "claude-sonnet-4-6"
        assert r["engine"] == "claude"
        assert "+09:00" in r["timestamp"]

    def test_ac2_task_failed(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        write_task_exit_audit(
            audit_path,
            event_type="task_failed",
            uuid=UUID,
            status="failure",
            elapsed_sec=5.0,
            token_count=200,
            model="claude-sonnet-4-6",
            engine="claude",
        )

        r = json.loads(audit_path.read_text().strip())
        assert r["event_type"] == "task_failed"
        assert r["status"] == "failure"

    def test_ac3_task_rejected(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        write_task_exit_audit(
            audit_path,
            event_type="task_rejected",
            uuid=UUID,
            status="rejected",
            elapsed_sec=3.0,
        )

        r = json.loads(audit_path.read_text().strip())
        assert r["event_type"] == "task_rejected"
        assert r["status"] == "rejected"

    def test_ac4_task_dep_failed_all_none(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        write_task_exit_audit(
            audit_path,
            event_type="task_dep_failed",
            uuid=UUID,
            status="dep_failed",
        )

        r = json.loads(audit_path.read_text().strip())
        assert r["event_type"] == "task_dep_failed"
        assert r["status"] == "dep_failed"
        assert r["elapsed_sec"] is None
        assert r["token_count"] is None
        assert r["model"] is None
        assert r["engine"] is None

    def test_ac5_task_empty_result(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        write_task_exit_audit(
            audit_path,
            event_type="task_empty_result",
            uuid=UUID,
            status="empty_result",
            elapsed_sec=1.0,
        )

        r = json.loads(audit_path.read_text().strip())
        assert r["event_type"] == "task_empty_result"
        assert r["status"] == "empty_result"

    def test_ac9_oserror_logs_stderr_no_exception(self, tmp_path, capsys):
        audit_path = tmp_path / "audit.jsonl"
        audit_path.mkdir()  # make it a directory so open() fails

        write_task_exit_audit(
            audit_path,
            event_type="task_complete",
            uuid=UUID,
            status="success",
        )

        captured = capsys.readouterr()
        assert "[audit] warning:" in captured.err

    def test_appends_multiple_calls(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        write_task_exit_audit(audit_path, event_type="task_complete", uuid=UUID, status="success")
        write_task_exit_audit(audit_path, event_type="task_failed", uuid=UUID, status="failure")

        lines = audit_path.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_schema_version_default_1(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        write_task_exit_audit(audit_path, event_type="task_complete", uuid=UUID, status="success")
        r = json.loads(audit_path.read_text().strip())
        assert r["schema_version"] == 1

    def test_ac10_coexists_with_enqueue_record(self, tmp_path):
        """AC-10: enqueue レコード（write_audit_log）と exit レコードが同一ファイルに共存できる。"""
        from ghdag.pipeline.audit import AuditContext, write_audit_log

        audit_path = tmp_path / "audit.jsonl"
        write_audit_log(
            audit_path,
            task_uuids=[UUID],
            exec_lines_count=1,
            context=AuditContext(source="issuesmith"),
        )
        write_task_exit_audit(audit_path, event_type="task_complete", uuid=UUID, status="success")

        lines = audit_path.read_text().strip().splitlines()
        assert len(lines) == 2
        enqueue_r = json.loads(lines[0])
        exit_r = json.loads(lines[1])
        assert "task_uuids" in enqueue_r
        assert exit_r["event_type"] == "task_complete"
