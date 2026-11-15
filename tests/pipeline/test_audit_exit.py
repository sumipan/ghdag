"""Tests for write_task_exit_audit() — Issue #960 AC-1, AC-2, ..., AC-9, Issue #1041."""

from __future__ import annotations

from ghdag.io.audit import write_task_exit_audit

UUID = "8cad156e-0000-0000-0000-000000000001"


class TestWriteTaskExitAudit:

    def test_write_task_exit_audit_oserror_logs_warning_only(self, tmp_path, capsys):
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

    # --- Issue #961 tests ---

    # --- Issue #962 tests ---

    # --- Issue #1041 tests ---
