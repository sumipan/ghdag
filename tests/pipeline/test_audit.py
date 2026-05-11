"""Tests for pipeline/audit.py — AC 1-9 (Issue #863), AC 1-11 (Issue #762)."""

from __future__ import annotations

import json
import re
from pathlib import Path

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
    def test_ac4_keyword_args_recorded(self, tmp_path):
        """AC4: task_uuids, exec_lines_count をキーワード引数で渡し正しく記録される。"""
        audit_path = tmp_path / "audit.jsonl"
        ctx = AuditContext(source="issuesmith")

        write_audit_log(
            audit_path,
            task_uuids=[UUID1],
            exec_lines_count=1,
            context=ctx,
        )

        assert audit_path.exists()
        records = [json.loads(line) for line in audit_path.read_text().splitlines()]
        assert len(records) == 1
        r = records[0]
        assert r["task_uuids"] == [UUID1]
        assert r["exec_lines_count"] == 1
        assert r["source"] == "issuesmith"
        assert "+09:00" in r["timestamp"]
        assert isinstance(r["caller_stack"], list)

    def test_ac5_exec_lines_count_zero_no_write(self, tmp_path):
        """AC5: exec_lines_count=0 → 何も書き込まれない。"""
        audit_path = tmp_path / "audit.jsonl"
        ctx = AuditContext()

        write_audit_log(
            audit_path,
            task_uuids=[],
            exec_lines_count=0,
            context=ctx,
        )

        assert not audit_path.exists()

    def test_ac5_empty_task_uuids_with_count(self, tmp_path):
        """AC5補: task_uuids=[] かつ exec_lines_count > 0 → 空リストとして記録される。"""
        audit_path = tmp_path / "audit.jsonl"
        ctx = AuditContext(source="issuesmith")

        write_audit_log(
            audit_path,
            task_uuids=[],
            exec_lines_count=1,
            context=ctx,
        )

        r = json.loads(audit_path.read_text().strip())
        assert r["task_uuids"] == []
        assert r["exec_lines_count"] == 1

    def test_ac8_write_failure_logs_stderr_no_exception(self, tmp_path, capsys):
        """AC8: I/O 失敗 → stderr 警告のみ、例外を上位に伝搬しない。"""
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

    def test_without_context_uses_unknown(self, tmp_path):
        """AuditContext デフォルト → source='unknown', correlation_id=null。"""
        audit_path = tmp_path / "audit.jsonl"

        write_audit_log(
            audit_path,
            task_uuids=[UUID1],
            exec_lines_count=1,
            context=AuditContext(),
        )

        r = json.loads(audit_path.read_text().strip())
        assert r["source"] == "unknown"
        assert r["correlation_id"] is None

    def test_idempotency_key_recorded(self, tmp_path):
        """idempotency_key が渡された場合、ログに反映される。"""
        audit_path = tmp_path / "audit.jsonl"

        write_audit_log(
            audit_path,
            task_uuids=[UUID1],
            exec_lines_count=1,
            context=AuditContext(source="issuesmith"),
            idempotency_key="issuesmith:brushup:756",
        )

        r = json.loads(audit_path.read_text().strip())
        assert r["idempotency_key"] == "issuesmith:brushup:756"

    def test_caller_stack_max_5_frames(self, tmp_path):
        """caller_stack は最大 5 フレーム。"""
        audit_path = tmp_path / "audit.jsonl"

        write_audit_log(
            audit_path,
            task_uuids=[UUID1],
            exec_lines_count=1,
            context=AuditContext(),
        )

        r = json.loads(audit_path.read_text().strip())
        assert len(r["caller_stack"]) <= 5

    def test_appends_multiple_calls(self, tmp_path):
        """複数回呼ぶと JSONL に複数行が追記される。"""
        audit_path = tmp_path / "audit.jsonl"

        write_audit_log(
            audit_path,
            task_uuids=[UUID1],
            exec_lines_count=1,
            context=AuditContext(),
        )
        write_audit_log(
            audit_path,
            task_uuids=[UUID2],
            exec_lines_count=1,
            context=AuditContext(),
        )

        lines = audit_path.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_ac1_no_extract_task_uuids_in_audit(self):
        """AC1: audit モジュールに _extract_task_uuids が存在しない。"""
        import ghdag.pipeline.audit as audit_mod
        assert not hasattr(audit_mod, "_extract_task_uuids")

    def test_ac2_no_uuid_re_in_audit(self):
        """AC2: audit モジュールに _UUID_RE が存在しない。"""
        import ghdag.pipeline.audit as audit_mod
        assert not hasattr(audit_mod, "_UUID_RE")

    def test_ac3_no_exec_lines_param(self):
        """AC3: write_audit_log が exec_lines パラメータを持たない。"""
        import inspect
        sig = inspect.signature(write_audit_log)
        assert "exec_lines" not in sig.parameters


class TestAppendExecRecordsAudit:
    """AC6: append_exec_records 経由で dict から UUID が抽出され audit に記録される。"""

    def test_ac6_uuid_from_dict(self, tmp_path):
        from ghdag.pipeline.state import PipelineState

        exec_path = tmp_path / "exec.jsonl"
        state = PipelineState(state_dir=tmp_path / ".state", exec_md_path=exec_path)
        ctx = AuditContext(source="issuesmith")
        records = [{"uuid": UUID1, "command": "claude -p --force < order.md"}]

        state.append_exec_records(records, audit_context=ctx)

        audit_path = tmp_path / "audit.jsonl"
        assert audit_path.exists()
        r = json.loads(audit_path.read_text().strip())
        assert UUID1 in r["task_uuids"]
        assert r["exec_lines_count"] == 1

    def test_ac6_record_without_uuid_key(self, tmp_path):
        """uuid キーを持たない dict はスキップ。"""
        from ghdag.pipeline.state import PipelineState

        exec_path = tmp_path / "exec.jsonl"
        state = PipelineState(state_dir=tmp_path / ".state", exec_md_path=exec_path)
        records = [{"command": "cmd", "idempotency_key": "k"}]

        state.append_exec_records(records, audit_context=AuditContext())

        audit_path = tmp_path / "audit.jsonl"
        r = json.loads(audit_path.read_text().strip())
        assert r["task_uuids"] == []


class TestAppendExecAudit:
    """AC7: append_exec 経由でテキスト行から UUID が正規表現で抽出され audit に記録される。"""

    def test_ac7_uuid_from_text_line(self, tmp_path):
        from ghdag.pipeline.state import PipelineState

        exec_path = tmp_path / "exec.md"
        state = PipelineState(state_dir=tmp_path / ".state", exec_md_path=exec_path)
        ctx = AuditContext(source="issuesmith")
        lines = [f"{UUID1}: claude -p --force < order.md"]

        state.append_exec(lines, audit_context=ctx)

        audit_path = tmp_path / "audit.jsonl"
        assert audit_path.exists()
        r = json.loads(audit_path.read_text().strip())
        assert UUID1 in r["task_uuids"]
        assert r["exec_lines_count"] == 1

    def test_ac7_line_without_uuid_skipped(self, tmp_path):
        """UUID を持たないテキスト行は task_uuids に含まれない。"""
        from ghdag.pipeline.state import PipelineState

        exec_path = tmp_path / "exec.md"
        state = PipelineState(state_dir=tmp_path / ".state", exec_md_path=exec_path)
        lines = ["# idempotency: issuesmith:brushup:756"]

        state.append_exec(lines, audit_context=AuditContext())

        audit_path = tmp_path / "audit.jsonl"
        r = json.loads(audit_path.read_text().strip())
        assert r["task_uuids"] == []
        assert r["exec_lines_count"] == 1


_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class TestWriteLlmAuditLog:
    """Tests for write_llm_audit_log() — Issue #762."""

    def test_ac1_all_fields(self, tmp_path):
        """AC1: 全フィールド指定 — 正しく記録される。"""
        from ghdag.pipeline.audit import write_llm_audit_log

        audit_path = tmp_path / "audit.jsonl"
        write_llm_audit_log(
            audit_path,
            engine="claude",
            model="claude-sonnet-4-6",
            exit_code=0,
            correlation_id="slack:1234",
            timeout_sec=120,
        )

        assert audit_path.exists()
        records = [json.loads(line) for line in audit_path.read_text().splitlines()]
        assert len(records) == 1
        r = records[0]
        assert r["event"] == "llm_call"
        assert r["source"] == "llm_cli"
        assert r["engine"] == "claude"
        assert r["model"] == "claude-sonnet-4-6"
        assert r["exit_code"] == 0
        assert r["correlation_id"] == "slack:1234"
        assert r["timeout_sec"] == 120
        assert "+09:00" in r["timestamp"]
        assert _UUID4_RE.match(r["request_id"])

    def test_ac2_correlation_id_none(self, tmp_path):
        """AC2: correlation_id 未指定 → null。"""
        from ghdag.pipeline.audit import write_llm_audit_log

        audit_path = tmp_path / "audit.jsonl"
        write_llm_audit_log(
            audit_path,
            engine="claude",
            model="claude-sonnet-4-6",
            exit_code=0,
        )

        r = json.loads(audit_path.read_text().strip())
        assert r["correlation_id"] is None

    def test_ac6_timeout_sec_none(self, tmp_path):
        """AC6: timeout_sec 未指定 → null。"""
        from ghdag.pipeline.audit import write_llm_audit_log

        audit_path = tmp_path / "audit.jsonl"
        write_llm_audit_log(
            audit_path,
            engine="claude",
            model="claude-sonnet-4-6",
            exit_code=0,
        )

        r = json.loads(audit_path.read_text().strip())
        assert r["timeout_sec"] is None

    def test_ac8_write_failure_logs_stderr_no_exception(self, tmp_path, capsys):
        """AC8: I/O 失敗 → stderr 警告のみ、例外を上位に伝搬しない。"""
        from ghdag.pipeline.audit import write_llm_audit_log

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

    def test_request_id_unique_per_call(self, tmp_path):
        """request_id は呼び出しごとに異なる UUID4。"""
        from ghdag.pipeline.audit import write_llm_audit_log

        audit_path = tmp_path / "audit.jsonl"
        for _ in range(3):
            write_llm_audit_log(
                audit_path,
                engine="claude",
                model="claude-sonnet-4-6",
                exit_code=0,
            )

        records = [json.loads(line) for line in audit_path.read_text().splitlines()]
        request_ids = [r["request_id"] for r in records]
        assert len(set(request_ids)) == 3

    def test_appends_multiple_calls(self, tmp_path):
        """複数回呼ぶと JSONL に複数行が追記される。"""
        from ghdag.pipeline.audit import write_llm_audit_log

        audit_path = tmp_path / "audit.jsonl"
        write_llm_audit_log(audit_path, engine="claude", model="claude-sonnet-4-6", exit_code=0)
        write_llm_audit_log(audit_path, engine="cursor", model="auto", exit_code=0)

        lines = audit_path.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_ac11_coexists_with_enqueue_records(self, tmp_path):
        """AC11: enqueue レコードと llm_call レコードが同一ファイルに共存できる。"""
        from ghdag.pipeline.audit import write_llm_audit_log

        audit_path = tmp_path / "audit.jsonl"
        write_audit_log(
            audit_path,
            task_uuids=[UUID1],
            exec_lines_count=1,
            context=AuditContext(source="issuesmith"),
        )
        write_llm_audit_log(audit_path, engine="claude", model="claude-sonnet-4-6", exit_code=0)

        lines = audit_path.read_text().strip().splitlines()
        assert len(lines) == 2
        enqueue_r = json.loads(lines[0])
        llm_r = json.loads(lines[1])
        assert "event" not in enqueue_r
        assert llm_r["event"] == "llm_call"
