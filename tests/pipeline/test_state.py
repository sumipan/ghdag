"""Tests for pipeline/state.py — append_exec audit integration (Issue #756)."""

from __future__ import annotations

import json

import pytest

from ghdag.pipeline.audit import AuditContext
from ghdag.pipeline.state import PipelineState


UUID1 = "38d6b791-1072-42f0-838d-45c7d10748ff"
UUID2 = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"


@pytest.fixture
def pipeline(tmp_path):
    exec_md = tmp_path / "exec.md"
    exec_md.write_text("", encoding="utf-8")
    return PipelineState(state_dir=tmp_path / "state", exec_md_path=exec_md)


class TestAppendExecAuditIntegration:
    def test_ac1_writes_exec_and_audit_with_context(self, pipeline, tmp_path):
        """AC1: AuditContext 指定 → exec.md 追記 + audit.jsonl 記録。"""
        lines = [f"{UUID1}: claude -p --force < order.md"]
        ctx = AuditContext(source="issuesmith", correlation_id="issue:756")

        pipeline.append_exec(lines, audit_context=ctx)

        exec_md = pipeline._exec_md_path
        assert UUID1 in exec_md.read_text()

        audit_path = exec_md.parent / "audit.jsonl"
        assert audit_path.exists()
        r = json.loads(audit_path.read_text().strip())
        assert r["source"] == "issuesmith"
        assert r["correlation_id"] == "issue:756"
        assert r["task_uuids"] == [UUID1]
        assert len(r["caller_stack"]) > 0
        assert "+09:00" in r["timestamp"]

    def test_ac2_no_context_uses_unknown(self, pipeline):
        """AC2: audit_context 未指定 → source='unknown', correlation_id=null。"""
        lines = [f"{UUID1}: cmd"]
        pipeline.append_exec(lines)

        audit_path = pipeline._exec_md_path.parent / "audit.jsonl"
        r = json.loads(audit_path.read_text().strip())
        assert r["source"] == "unknown"
        assert r["correlation_id"] is None

    def test_ac7_backward_compatible_no_audit_context(self, pipeline):
        """AC7: 後方互換 — audit_context なしで呼んでも exec.md 追記は正常。"""
        lines = [f"{UUID1}: cmd"]
        pipeline.append_exec(lines)  # no audit_context arg

        assert UUID1 in pipeline._exec_md_path.read_text()

    def test_exec_write_before_audit(self, pipeline):
        """exec.md 追記が audit より先に完了する（audit I/O 失敗でも exec は成功）。"""
        audit_dir = pipeline._exec_md_path.parent / "audit.jsonl"
        audit_dir.mkdir()  # make audit.jsonl a dir → I/O will fail

        lines = [f"{UUID1}: cmd"]
        pipeline.append_exec(lines, audit_context=AuditContext())

        # exec.md must be written even when audit fails
        assert UUID1 in pipeline._exec_md_path.read_text()
