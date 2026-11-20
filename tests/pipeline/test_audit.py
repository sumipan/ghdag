"""Tests for pipeline/audit.py — AC 1-9 (Issue #863), AC 1-11 (Issue #762), AC 1-7 (Issue #1046)."""

from __future__ import annotations

import json

from ghdag.io.audit import AuditContext, write_audit_log

UUID1 = "38d6b791-1072-42f0-838d-45c7d10748ff"


class TestAuditContext:
    def test_defaults(self):
        ctx = AuditContext()
        assert ctx.source == "unknown"
        assert ctx.correlation_id is None
        assert ctx.request_id is None
        assert ctx.parent_correlation_id is None
        assert ctx.orchestration_id is None

    def test_custom_values(self):
        ctx = AuditContext(source="issuesmith", correlation_id="issue:756")
        assert ctx.source == "issuesmith"
        assert ctx.correlation_id == "issue:756"

    def test_ac_a1_extended_fields_no_type_error(self):
        """AC-A1: passing request_id / parent_correlation_id / orchestration_id does not raise TypeError."""
        ctx = AuditContext(
            source="x",
            correlation_id="y",
            request_id="r",
            parent_correlation_id="p",
            orchestration_id="o",
        )
        assert ctx.request_id == "r"
        assert ctx.parent_correlation_id == "p"
        assert ctx.orchestration_id == "o"


class TestWriteAuditLog:

    def test_write_audit_log_skips_write_when_exec_lines_count_zero(self, tmp_path):
        """AC5: exec_lines_count=0 → nothing is written."""
        audit_path = tmp_path / "audit.jsonl"
        ctx = AuditContext()

        write_audit_log(
            audit_path,
            task_uuids=[],
            exec_lines_count=0,
            context=ctx,
        )

        assert not audit_path.exists()

    def test_write_audit_log_io_failure_logs_warning_only(self, tmp_path, capsys):
        """AC8: I/O failure → stderr warning only; exception is not propagated."""
        audit_path = tmp_path / "audit.jsonl"
        audit_path.mkdir()

        write_audit_log(
            audit_path,
            task_uuids=[UUID1],
            exec_lines_count=1,
            context=AuditContext(),
        )

        captured = capsys.readouterr()
        assert "[audit] warning:" in captured.err

class TestAppendExecRecordsAudit:
    """AC6: UUIDs extracted from dicts via enqueue_records are recorded in audit."""

    def test_enqueue_records_skips_dicts_without_uuid(self, tmp_path):
        """Dicts without a uuid key are skipped."""
        from ghdag.pipeline.task_queue import TaskQueue

        exec_path = tmp_path / "exec.jsonl"
        state = TaskQueue(state_dir=tmp_path / ".state", exec_jsonl_path=exec_path)
        records = [{"command": "cmd", "idempotency_key": "k"}]

        state.enqueue_records(records, audit_context=AuditContext())

        audit_path = tmp_path / "audit.jsonl"
        r = json.loads(audit_path.read_text().strip())
        assert r["task_uuids"] == []

    def test_ac_a5_request_id_in_exec_annotations(self, tmp_path):
        """AC-A5: enqueue_records injects request_id into annotations._request_id."""
        from ghdag.pipeline.task_queue import TaskQueue

        exec_path = tmp_path / "exec.jsonl"
        state = TaskQueue(state_dir=tmp_path / ".state", exec_jsonl_path=exec_path)
        records = [{"uuid": UUID1, "command": "echo hi"}]
        ctx = AuditContext(source="mltgnt-scheduler", request_id="req-abc")

        state.enqueue_records(records, audit_context=ctx)

        line = exec_path.read_text().strip()
        rec = json.loads(line)
        assert rec["annotations"]["_request_id"] == "req-abc"


class TestWriteLlmAuditLog:
    """Tests for write_llm_audit_log() — Issue #762."""

    def test_write_llm_audit_log_io_failure_logs_warning_only(self, tmp_path, capsys):
        """AC8: I/O failure → stderr warning only; exception is not propagated."""
        from ghdag.io.audit import write_llm_audit_log

        audit_path = tmp_path / "audit.jsonl"
        audit_path.mkdir()

        write_llm_audit_log(
            audit_path,
            engine="claude",
            model="claude-sonnet-4-6",
            exit_code=0,
        )

        captured = capsys.readouterr()
        assert "[audit] warning:" in captured.err


# ---------------------------------------------------------------------------
# Rotation (Issue #1046) — AC-1 through AC-7
# ---------------------------------------------------------------------------

class TestRotation:

    def test_audit_rotation_oserror_still_appends_record(self, tmp_path, monkeypatch, capsys):
        """AC-5: OSError during rotation → stderr warning; write still proceeds."""
        import ghdag.io._rotate as rotate_mod
        monkeypatch.setattr(rotate_mod, "_MAX_AUDIT_BYTES", 5)

        def bad_rotate(p):
            raise OSError("permission denied")
        monkeypatch.setattr(rotate_mod, "_do_rotate", bad_rotate)

        audit_path = tmp_path / "audit.jsonl"
        audit_path.write_text("x" * 10 + "\n")

        write_audit_log(audit_path, task_uuids=[], exec_lines_count=1, context=AuditContext())

        captured = capsys.readouterr()
        assert "[audit] warning:" in captured.err
        json_records = []
        for line in audit_path.read_text().splitlines():
            try:
                json_records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        assert any(r.get("exec_lines_count") == 1 for r in json_records)

    def test_write_task_exit_audit_triggers_size_rotation(self, tmp_path, monkeypatch):
        """AC-6: write_task_exit_audit triggers rotation."""
        import ghdag.io._rotate as rotate_mod
        from ghdag.io.audit import write_task_exit_audit
        monkeypatch.setattr(rotate_mod, "_MAX_AUDIT_BYTES", 5)

        audit_path = tmp_path / "audit.jsonl"
        audit_path.write_text("x" * 10 + "\n")

        write_task_exit_audit(audit_path, event_type="task_complete", uuid="u1", status="success")

        assert len(sorted(tmp_path.glob("audit.*.jsonl"))) == 1


class TestAuditRecordKeySchema:
    """Refs sumipan/ghdag#7552 — audit rows use event_type instead of event."""

    def test_write_llm_audit_log_persists_event_type(self, tmp_path):
        from ghdag.io.audit import write_llm_audit_log

        audit_path = tmp_path / "audit.jsonl"
        write_llm_audit_log(audit_path, engine="claude", model="m", exit_code=0)
        record = json.loads(audit_path.read_text(encoding="utf-8").strip())
        assert record["event_type"] == "llm_call"
        assert "event" not in record

    def test_write_rate_limit_audit_persists_event_type(self, tmp_path):
        from ghdag.io.audit import write_rate_limit_audit

        audit_path = tmp_path / "audit.jsonl"
        write_rate_limit_audit(
            audit_path, remaining=1, limit=100, reset=0,
        )
        record = json.loads(audit_path.read_text(encoding="utf-8").strip())
        assert record["event_type"] == "github_rate_limit"
        assert "event" not in record

    def test_md_write_audit_persists_event_type(self, tmp_path):
        from ghdag.files.writer import write_md_write_audit

        audit_path = tmp_path / "audit.jsonl"
        write_md_write_audit(audit_path, path="out.md", bytes_written=3)
        record = json.loads(audit_path.read_text(encoding="utf-8").strip())
        assert record["event_type"] == "md_write"
        assert "event" not in record
