"""Tests for write_task_exit_audit() — Issue #960 AC-1, AC-2, ..., AC-9, Issue #1041."""

from __future__ import annotations

import json

from ghdag.metrics.models import FailureClass
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

    # --- Issue #961 tests ---

    def test_ac2_exit_audit_with_correlation_id(self, tmp_path):
        """AC-2: write_task_exit_audit に correlation_id を渡すとレコードに含まれる。"""
        audit_path = tmp_path / "audit.jsonl"
        write_task_exit_audit(
            audit_path,
            event_type="task_complete",
            uuid=UUID,
            status="success",
            correlation_id="test:key",
        )

        r = json.loads(audit_path.read_text().strip())
        assert r["correlation_id"] == "test:key"

    def test_ac3_enqueue_and_exit_same_correlation_id(self, tmp_path):
        """AC-3: enqueue と exit レコードが同じ correlation_id を持つ。"""
        from ghdag.pipeline.audit import AuditContext, write_audit_log

        audit_path = tmp_path / "audit.jsonl"
        write_audit_log(
            audit_path,
            task_uuids=[UUID],
            exec_lines_count=1,
            context=AuditContext(source="issuesmith", correlation_id="test:key"),
        )
        write_task_exit_audit(
            audit_path,
            event_type="task_complete",
            uuid=UUID,
            status="success",
            correlation_id="test:key",
        )

        lines = audit_path.read_text().strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            r = json.loads(line)
            assert r["correlation_id"] == "test:key"

    def test_exit_audit_default_no_correlation_id(self, tmp_path):
        """correlation_id を省略した場合、レコードに含まれないか null になる。"""
        audit_path = tmp_path / "audit.jsonl"
        write_task_exit_audit(
            audit_path,
            event_type="task_complete",
            uuid=UUID,
            status="success",
        )

        r = json.loads(audit_path.read_text().strip())
        # デフォルト None → JSON では null or フィールドなし
        assert r.get("correlation_id") is None

    # --- Issue #962 tests ---

    def test_failure_class_in_record(self, tmp_path):
        """failure_class=FailureClass.TIMEOUT → JSON レコードに "failure_class": "TIMEOUT" が含まれる。"""
        audit_path = tmp_path / "audit.jsonl"
        write_task_exit_audit(
            audit_path,
            event_type="task_failed",
            uuid=UUID,
            status="failure",
            failure_class=FailureClass.TIMEOUT,
        )

        r = json.loads(audit_path.read_text().strip())
        assert r["failure_class"] == "TIMEOUT"

    def test_failure_class_null_for_success(self, tmp_path):
        """failure_class=None → JSON レコードに "failure_class": null が含まれる。"""
        audit_path = tmp_path / "audit.jsonl"
        write_task_exit_audit(
            audit_path,
            event_type="task_complete",
            uuid=UUID,
            status="success",
            failure_class=None,
        )

        r = json.loads(audit_path.read_text().strip())
        assert r["failure_class"] is None

    def test_failure_class_default_null(self, tmp_path):
        """failure_class 未指定 → JSON レコードに "failure_class": null が含まれる。"""
        audit_path = tmp_path / "audit.jsonl"
        write_task_exit_audit(
            audit_path,
            event_type="task_complete",
            uuid=UUID,
            status="success",
        )

        r = json.loads(audit_path.read_text().strip())
        assert r["failure_class"] is None

    # --- Issue #1041 tests ---

    def test_failure_class_enum_serialized_as_string(self, tmp_path):
        """write_task_exit_audit(failure_class=FailureClass.TIMEOUT) → JSON で "failure_class": "TIMEOUT"。"""
        audit_path = tmp_path / "audit.jsonl"
        write_task_exit_audit(
            audit_path,
            event_type="task_failed",
            uuid=UUID,
            status="failure",
            failure_class=FailureClass.TIMEOUT,
        )

        r = json.loads(audit_path.read_text().strip())
        assert r["failure_class"] == "TIMEOUT"

    def test_failure_class_enum_none_serialized_as_null(self, tmp_path):
        """write_task_exit_audit(failure_class=None) → JSON で "failure_class": null。"""
        audit_path = tmp_path / "audit.jsonl"
        write_task_exit_audit(
            audit_path,
            event_type="task_complete",
            uuid=UUID,
            status="success",
            failure_class=None,
        )

        r = json.loads(audit_path.read_text().strip())
        assert r["failure_class"] is None
