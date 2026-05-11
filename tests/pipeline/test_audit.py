"""Tests for pipeline/audit.py — AC 1-7 (Issue #756), AC 1-11 (Issue #762)."""

from __future__ import annotations

import json
import re

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
        records = [json.loads(line) for line in audit_path.read_text().splitlines()]
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


class TestExtractTaskUuidsJsonFormat:
    """Tests for _extract_task_uuids — Issue #860 (JSON format support)."""

    def test_ac1_json_format_uuid_extracted(self, tmp_path):
        """AC1: JSON 形式の exec 行から task_uuids に UUID が記録される。"""
        audit_path = tmp_path / "audit.jsonl"
        line = json.dumps({"uuid": UUID1, "command": "claude -p --force < order.md"})
        ctx = AuditContext(source="issuesmith")

        write_audit_log(audit_path, [line], ctx)

        r = json.loads(audit_path.read_text().strip())
        assert r["task_uuids"] == [UUID1]

    def test_ac2_text_format_backward_compat(self, tmp_path):
        """AC2: 旧テキスト形式からも UUID が正しく抽出される（後方互換）。"""
        audit_path = tmp_path / "audit.jsonl"
        line = f"{UUID1}: claude -p --force < order.md"
        ctx = AuditContext(source="issuesmith")

        write_audit_log(audit_path, [line], ctx)

        r = json.loads(audit_path.read_text().strip())
        assert r["task_uuids"] == [UUID1]

    def test_ac3_mixed_formats(self, tmp_path):
        """AC3: JSON 形式とテキスト形式の混在リストからすべての UUID を抽出。"""
        audit_path = tmp_path / "audit.jsonl"
        lines = [
            json.dumps({"uuid": UUID1, "command": "cmd1"}),
            f"{UUID2}: cmd2",
        ]
        ctx = AuditContext(source="issuesmith")

        write_audit_log(audit_path, lines, ctx)

        r = json.loads(audit_path.read_text().strip())
        assert UUID1 in r["task_uuids"]
        assert UUID2 in r["task_uuids"]
        assert len(r["task_uuids"]) == 2

    def test_ac4_json_without_uuid_key_skipped(self, tmp_path):
        """AC4: "uuid" キーを持たない JSON 行は task_uuids に含まれない。"""
        audit_path = tmp_path / "audit.jsonl"
        line = json.dumps({"comment": "skip"})
        ctx = AuditContext(source="issuesmith")

        write_audit_log(audit_path, [line], ctx)

        r = json.loads(audit_path.read_text().strip())
        assert r["task_uuids"] == []

    def test_ac5_broken_json_falls_back_to_regex(self, tmp_path):
        """AC5: 不正 JSON はフォールバックで _UUID_RE を試行し、マッチしなければスキップ。"""
        audit_path = tmp_path / "audit.jsonl"
        broken = "{broken"
        ctx = AuditContext(source="issuesmith")

        write_audit_log(audit_path, [broken], ctx)

        r = json.loads(audit_path.read_text().strip())
        assert r["task_uuids"] == []

    def test_ac6_empty_string_line_skipped(self, tmp_path):
        """AC6: 空文字列の行はスキップされる。"""
        audit_path = tmp_path / "audit.jsonl"
        ctx = AuditContext(source="issuesmith")

        write_audit_log(audit_path, ["", "   "], ctx)

        r = json.loads(audit_path.read_text().strip())
        assert r["task_uuids"] == []

    def test_ac7_empty_exec_lines_no_write(self, tmp_path):
        """AC7: exec_lines が空リストの場合、write_audit_log は何も書き込まない。"""
        audit_path = tmp_path / "audit.jsonl"
        ctx = AuditContext(source="issuesmith")

        write_audit_log(audit_path, [], ctx)

        assert not audit_path.exists()


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
        audit_path.mkdir()  # directory → open() fails

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
        write_audit_log(audit_path, [f"{UUID1}: cmd"], AuditContext(source="issuesmith"))
        write_llm_audit_log(audit_path, engine="claude", model="claude-sonnet-4-6", exit_code=0)

        lines = audit_path.read_text().strip().splitlines()
        assert len(lines) == 2
        enqueue_r = json.loads(lines[0])
        llm_r = json.loads(lines[1])
        assert "event" not in enqueue_r
        assert llm_r["event"] == "llm_call"
