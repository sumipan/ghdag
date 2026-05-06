"""Tests for pipeline/audit.py — AC 1-7 (Issue #756)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ghdag.pipeline.audit import AuditContext, write_audit_log


UUID1 = "38d6b791-1072-42f0-838d-45c7d10748ff"
UUID2 = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"


class TestAuditContext:
    def test_defaults(self):
        ctx = AuditContext()
        assert ctx.source == "unknown"
        assert ctx.correlation_id is None

    def test_custom_values(self):
        ctx = AuditContext(source="issuesmith", correlation_id="issue:756")
        assert ctx.source == "issuesmith"
        assert ctx.correlation_id == "issue:756"


class TestWriteAuditLog:
    def test_ac1_with_context(self, tmp_path):
        """AC1: AuditContext 指定あり — 各フィールドが正しく記録される。"""
        audit_path = tmp_path / "audit.jsonl"
        lines = [f"{UUID1}: claude -p --force < order.md"]
        ctx = AuditContext(source="issuesmith", correlation_id="issue:756")

        write_audit_log(audit_path, lines, ctx)

        assert audit_path.exists()
        records = [json.loads(l) for l in audit_path.read_text().splitlines()]
        assert len(records) == 1
        r = records[0]
        assert r["source"] == "issuesmith"
        assert r["correlation_id"] == "issue:756"
        assert r["task_uuids"] == [UUID1]
        assert isinstance(r["caller_stack"], list)
        assert len(r["caller_stack"]) > 0
        # timestamp must be ISO 8601 with +09:00
        assert "+09:00" in r["timestamp"]
        assert r["exec_lines_count"] == 1

    def test_ac2_without_context_uses_unknown(self, tmp_path):
        """AC2: AuditContext 未指定 → source='unknown', correlation_id=null."""
        audit_path = tmp_path / "audit.jsonl"
        lines = [f"{UUID1}: claude -p --force < order.md"]
        ctx = AuditContext()  # defaults

        write_audit_log(audit_path, lines, ctx)

        r = json.loads(audit_path.read_text().strip())
        assert r["source"] == "unknown"
        assert r["correlation_id"] is None
        assert isinstance(r["caller_stack"], list)

    def test_ac3_multiple_lines(self, tmp_path):
        """AC3: 複数行 — exec_lines_count と task_uuids が全行を反映。"""
        audit_path = tmp_path / "audit.jsonl"
        lines = [
            f"{UUID1}: cmd1",
            f"{UUID2}: cmd2",
            "# idempotency: issuesmith:brushup:756",
        ]
        ctx = AuditContext(source="issuesmith")

        write_audit_log(audit_path, lines, ctx)

        r = json.loads(audit_path.read_text().strip())
        assert r["exec_lines_count"] == 3
        assert UUID1 in r["task_uuids"]
        assert UUID2 in r["task_uuids"]

    def test_ac4_write_failure_logs_stderr_no_exception(self, tmp_path, capsys):
        """AC4: I/O 失敗 → stderr 警告のみ、例外を上位に伝搬しない。"""
        audit_path = tmp_path / "audit.jsonl"
        audit_path.mkdir()  # make it a directory so open() fails

        lines = [f"{UUID1}: cmd"]
        ctx = AuditContext()

        # must not raise
        write_audit_log(audit_path, lines, ctx)

        captured = capsys.readouterr()
        assert "[audit] warning:" in captured.err

    def test_ac5_empty_lines_no_log(self, tmp_path):
        """AC5: 空リスト → 監査ログは記録されない。"""
        audit_path = tmp_path / "audit.jsonl"
        ctx = AuditContext()

        write_audit_log(audit_path, [], ctx)

        assert not audit_path.exists()

    def test_ac6_no_uuid_lines(self, tmp_path):
        """AC6: UUID なし行 → task_uuids は空リスト。"""
        audit_path = tmp_path / "audit.jsonl"
        lines = ["# idempotency: issuesmith:brushup:756"]
        ctx = AuditContext(source="issuesmith")

        write_audit_log(audit_path, lines, ctx)

        r = json.loads(audit_path.read_text().strip())
        assert r["task_uuids"] == []
        assert r["exec_lines_count"] == 1

    def test_idempotency_key_recorded(self, tmp_path):
        """idempotency_key が渡された場合、ログに反映される。"""
        audit_path = tmp_path / "audit.jsonl"
        lines = [f"{UUID1}: cmd"]
        ctx = AuditContext(source="issuesmith")

        write_audit_log(audit_path, lines, ctx, idempotency_key="issuesmith:brushup:756")

        r = json.loads(audit_path.read_text().strip())
        assert r["idempotency_key"] == "issuesmith:brushup:756"

    def test_caller_stack_max_5_frames(self, tmp_path):
        """caller_stack は最大 5 フレーム。"""
        audit_path = tmp_path / "audit.jsonl"
        write_audit_log(audit_path, [f"{UUID1}: cmd"], AuditContext())

        r = json.loads(audit_path.read_text().strip())
        assert len(r["caller_stack"]) <= 5

    def test_appends_multiple_calls(self, tmp_path):
        """複数回呼ぶと JSONL に複数行が追記される。"""
        audit_path = tmp_path / "audit.jsonl"
        write_audit_log(audit_path, [f"{UUID1}: cmd"], AuditContext())
        write_audit_log(audit_path, [f"{UUID2}: cmd"], AuditContext())

        lines = audit_path.read_text().strip().splitlines()
        assert len(lines) == 2
