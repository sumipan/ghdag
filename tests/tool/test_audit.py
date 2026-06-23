"""Tests for ghdag.tool.audit — tool fallback audit logging."""

from __future__ import annotations

import json
from pathlib import Path

from ghdag.tool.audit import write_tool_fallback_audit


class TestWriteToolFallbackAudit:
    def test_writes_json_line_with_required_fields(self, tmp_path: Path) -> None:
        audit_path = tmp_path / "audit.jsonl"
        write_tool_fallback_audit(
            audit_path,
            tool="code_review",
            original_engine="claude-code",
            original_model="claude-opus-4-7",
            fallback_engine="claude-code",
            fallback_model="claude-sonnet-4-6",
            fallback_index=0,
            reason="model_unavailable",
        )

        lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

        record = json.loads(lines[0])
        assert record["schema_version"] == 1
        assert record["event"] == "tool.fallback"
        assert record["tool"] == "code_review"
        assert record["original_engine"] == "claude-code"
        assert record["original_model"] == "claude-opus-4-7"
        assert record["fallback_engine"] == "claude-code"
        assert record["fallback_model"] == "claude-sonnet-4-6"
        assert record["fallback_index"] == 0
        assert record["reason"] == "model_unavailable"
        assert "timestamp" in record
        assert "uuid" in record

    def test_includes_correlation_id_when_provided(self, tmp_path: Path) -> None:
        audit_path = tmp_path / "audit.jsonl"
        write_tool_fallback_audit(
            audit_path,
            tool="t",
            original_engine="claude-code",
            original_model="claude-opus-4-7",
            fallback_engine="claude-code",
            fallback_model="claude-sonnet-4-6",
            fallback_index=0,
            reason="rate_limited",
            correlation_id="corr-123",
        )

        record = json.loads(audit_path.read_text(encoding="utf-8").strip())
        assert record["correlation_id"] == "corr-123"
