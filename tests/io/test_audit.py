"""Tests for ghdag.io.audit — audit I/O consolidation (nexus Issue #2673)."""

from __future__ import annotations

import json
from pathlib import Path


class TestAppendAuditRecord:
    def test_appends_json_line(self, tmp_path: Path) -> None:
        from ghdag.io.audit import append_audit_record

        audit_path = tmp_path / "audit.jsonl"
        record = {"event": "test", "value": 1}
        append_audit_record(audit_path, record)

        lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"event": "test", "value": 1}

    def test_does_not_inject_extra_fields(self, tmp_path: Path) -> None:
        """Schema invariant: append adds no keys beyond the given record."""
        from ghdag.io.audit import append_audit_record

        audit_path = tmp_path / "audit.jsonl"
        record = {"event": "md_write", "timestamp": "2026-01-01T00:00:00+09:00"}
        append_audit_record(audit_path, record)

        written = json.loads(audit_path.read_text(encoding="utf-8").strip())
        assert set(written.keys()) == {"event", "timestamp"}

    def test_rotates_when_over_threshold(self, tmp_path: Path, monkeypatch) -> None:
        import ghdag.io._rotate as rotate_mod
        from ghdag.io.audit import append_audit_record

        monkeypatch.setattr(rotate_mod, "_MAX_AUDIT_BYTES", 5)
        audit_path = tmp_path / "audit.jsonl"
        old = "x" * 10 + "\n"
        audit_path.write_text(old)

        append_audit_record(audit_path, {"event": "after"})

        rotated = sorted(tmp_path.glob("audit.*.jsonl"))
        assert len(rotated) == 1
        assert rotated[0].read_text() == old
        assert json.loads(audit_path.read_text(encoding="utf-8").strip())["event"] == "after"


class TestCompatImports:
    """Canonical audit helpers resolve from ghdag.io.audit."""

    def test_pipeline_audit_context(self) -> None:
        from ghdag.io.audit import AuditContext

        ctx = AuditContext(source="issuesmith", correlation_id="c1")
        assert ctx.source == "issuesmith"
        assert ctx.correlation_id == "c1"

    def test_pipeline_write_task_exit_audit(self, tmp_path: Path) -> None:
        from ghdag.io.audit import write_task_exit_audit

        audit_path = tmp_path / "audit.jsonl"
        write_task_exit_audit(
            audit_path,
            event_type="task_complete",
            uuid="u1",
            status="success",
        )
        rec = json.loads(audit_path.read_text(encoding="utf-8").strip())
        assert rec["event_type"] == "task_complete"
        assert rec["uuid"] == "u1"
        assert rec["status"] == "success"

    def test_pipeline_all_writers_resolvable(self) -> None:
        from ghdag.pipeline import audit as pa

        for name in (
            "AuditContext",
            "write_audit_log",
            "write_llm_audit_log",
            "write_llm_inference_audit",
            "write_task_retry_audit",
            "write_quarantine_audit",
            "write_task_exit_audit",
            "write_rate_limit_audit",
            "compute_prompt_hash",
        ):
            assert hasattr(pa, name), name


class TestWriteRepairTurnAudit:
    def test_record_shape(self, tmp_path: Path) -> None:
        from ghdag.io.audit import write_repair_turn_audit

        audit_path = tmp_path / "audit.jsonl"
        write_repair_turn_audit(
            audit_path,
            uuid="u",
            engine="claude",
            turn=1,
            code="C",
            message="m",
            hint="h",
        )
        rec = json.loads(audit_path.read_text(encoding="utf-8").strip())
        assert rec["event_type"] == "llm.repair_turn"
        assert rec["schema_version"] == 1
        assert rec["uuid"] == "u"
        assert rec["engine"] == "claude"
        assert rec["turn"] == 1
        assert rec["code"] == "C"
        assert rec["message"] == "m"
        assert rec["hint"] == "h"


class TestWritersUseAppendAuditRecord:
    def test_md_write_audit_uses_append(self, tmp_path: Path, monkeypatch) -> None:
        from ghdag.files import writer

        calls: list[tuple] = []

        def spy(path, record):
            calls.append((path, dict(record)))

        monkeypatch.setattr(writer, "append_audit_record", spy)

        audit_path = tmp_path / "audit.jsonl"
        writer.write_md_write_audit(audit_path, path="result/foo.md", bytes_written=10)

        assert len(calls) == 1
        assert calls[0][0] == audit_path
        assert calls[0][1]["event"] == "md_write"
        assert calls[0][1]["path"] == "result/foo.md"
        assert calls[0][1]["bytes_written"] == 10

    def test_promote_audit_uses_append(self, tmp_path: Path, monkeypatch) -> None:
        from ghdag.files import promote

        calls: list[tuple] = []

        def spy(path, record):
            calls.append((path, dict(record)))

        monkeypatch.setattr(promote, "append_audit_record", spy)

        audit_path = tmp_path / "audit.jsonl"
        promote._write_promote_audit(
            audit_path,
            source_path="order/a.md",
            target_path="result/a.md",
            section="Promoted",
            status="promoted",
        )

        assert len(calls) == 1
        assert calls[0][1]["event"] == "md_promote"
        assert calls[0][1]["source_path"] == "order/a.md"

    def test_tool_fallback_uses_append(self, tmp_path: Path, monkeypatch) -> None:
        from ghdag.tool import audit as tool_audit

        calls: list[tuple] = []

        def spy(path, record):
            calls.append((path, dict(record)))

        monkeypatch.setattr(tool_audit, "append_audit_record", spy)

        audit_path = tmp_path / "audit.jsonl"
        tool_audit.write_tool_fallback_audit(
            audit_path,
            tool="code_review",
            original_engine="claude-code",
            original_model="opus",
            fallback_engine="claude-code",
            fallback_model="sonnet",
            fallback_index=0,
            reason="model_unavailable",
        )

        assert len(calls) == 1
        assert calls[0][1]["event"] == "tool.fallback"
        assert calls[0][1]["tool"] == "code_review"
