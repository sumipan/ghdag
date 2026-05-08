"""Tests for pipeline/state.py — append_exec audit integration (Issue #756)."""

from __future__ import annotations

import json

import pytest

from ghdag.pipeline.audit import AuditContext
from ghdag.pipeline.state import PipelineState


UUID1 = "38d6b791-1072-42f0-838d-45c7d10748ff"
UUID2 = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
UUID3 = "ccccdddd-eeee-ffff-0000-111122223333"


@pytest.fixture
def pipeline(tmp_path):
    exec_md = tmp_path / "exec.md"
    exec_md.write_text("", encoding="utf-8")
    return PipelineState(state_dir=tmp_path / "state", exec_md_path=exec_md)


@pytest.fixture
def pipeline_jsonl(tmp_path):
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.write_text("", encoding="utf-8")
    return PipelineState(state_dir=tmp_path / "state", exec_md_path=exec_jsonl)


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


@pytest.fixture
def jsonl_pipeline(tmp_path):
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.write_text("", encoding="utf-8")
    return PipelineState(state_dir=tmp_path / "state", exec_md_path=exec_jsonl)


# ---------------------------------------------------------------------------
# JSONL モード: parse_exec_tasks / remove_exec_entries
# ---------------------------------------------------------------------------

class TestParseExecTasksJsonl:
    def test_returns_uuid_command_map(self, jsonl_pipeline):
        """JSONL モード: parse_exec_tasks が {uuid: command} を返す。"""
        records = [
            {"uuid": UUID1, "command": "claude -p order.md", "depends": []},
            {"uuid": UUID2, "command": "agent --force < order2.md", "depends": []},
        ]
        jsonl_pipeline._exec_md_path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n"
        )

        result = jsonl_pipeline.parse_exec_tasks()

        assert result == {UUID1: "claude -p order.md", UUID2: "agent --force < order2.md"}

    def test_skips_invalid_json_lines(self, jsonl_pipeline):
        """JSONL モード: 不正行はスキップされ例外が発生しない。"""
        jsonl_pipeline._exec_md_path.write_text(
            f'{{"uuid": "{UUID1}", "command": "cmd"}}\n'
            "this-is-not-json\n"
            f'{{"uuid": "{UUID2}", "command": "cmd2"}}\n'
        )

        result = jsonl_pipeline.parse_exec_tasks()

        assert UUID1 in result
        assert UUID2 in result

    def test_skips_missing_fields(self, jsonl_pipeline):
        """JSONL モード: uuid または command が欠けている行はスキップ。"""
        jsonl_pipeline._exec_md_path.write_text(
            f'{{"uuid": "{UUID1}", "command": "cmd"}}\n'
            f'{{"command": "no-uuid"}}\n'
            f'{{"uuid": "{UUID2}"}}\n'
        )

        result = jsonl_pipeline.parse_exec_tasks()

        assert result == {UUID1: "cmd"}

    def test_empty_file_returns_empty_dict(self, jsonl_pipeline):
        """JSONL モード: 空ファイル → 空辞書。"""
        assert jsonl_pipeline.parse_exec_tasks() == {}

    def test_text_mode_still_works(self, tmp_path):
        """テキスト形式（exec.md）は従来どおりパースできる。"""
        exec_md = tmp_path / "exec.md"
        exec_md.write_text(
            f"{UUID1}: claude -p order.md\n"
            f"# comment line\n"
            f"{UUID2}: agent order2.md\n"
        )
        state = PipelineState(state_dir=tmp_path / "state", exec_md_path=exec_md)
        result = state.parse_exec_tasks()
        assert result == {UUID1: "claude -p order.md", UUID2: "agent order2.md"}


class TestRemoveExecEntriesJsonl:
    def test_removes_matching_uuid(self, jsonl_pipeline):
        """JSONL モード: 指定 UUID のエントリが削除される。"""
        records = [
            {"uuid": UUID1, "command": "cmd1"},
            {"uuid": UUID2, "command": "cmd2"},
            {"uuid": UUID3, "command": "cmd3"},
        ]
        jsonl_pipeline._exec_md_path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n"
        )

        removed = jsonl_pipeline.remove_exec_entries({UUID1, UUID3})

        assert removed == 2
        remaining = jsonl_pipeline.parse_exec_tasks()
        assert UUID1 not in remaining
        assert UUID3 not in remaining
        assert UUID2 in remaining

    def test_returns_zero_when_no_match(self, jsonl_pipeline):
        """JSONL モード: 一致 UUID なし → 0 を返しファイルは変更されない。"""
        content = f'{{"uuid": "{UUID1}", "command": "cmd"}}\n'
        jsonl_pipeline._exec_md_path.write_text(content)

        removed = jsonl_pipeline.remove_exec_entries({"nonexistent-uuid"})

        assert removed == 0
        assert jsonl_pipeline._exec_md_path.read_text() == content

    def test_preserves_empty_lines(self, jsonl_pipeline):
        """JSONL モード: 空行は保持される。"""
        jsonl_pipeline._exec_md_path.write_text(
            f'{{"uuid": "{UUID1}", "command": "cmd"}}\n'
            "\n"
            f'{{"uuid": "{UUID2}", "command": "cmd2"}}\n'
        )

        jsonl_pipeline.remove_exec_entries({UUID1})

        remaining_text = jsonl_pipeline._exec_md_path.read_text()
        assert UUID1 not in remaining_text
        assert UUID2 in remaining_text

    def test_text_mode_removal_still_works(self, tmp_path):
        """テキスト形式（exec.md）の remove も従来どおり動作する。"""
        exec_md = tmp_path / "exec.md"
        exec_md.write_text(
            f"{UUID1}: cmd1\n"
            f"{UUID2}: cmd2\n"
        )
        state = PipelineState(state_dir=tmp_path / "state", exec_md_path=exec_md)

        removed = state.remove_exec_entries({UUID1})

        assert removed == 1
        assert UUID1 not in exec_md.read_text()
        assert UUID2 in exec_md.read_text()


# ---------------------------------------------------------------------------
# JSONL モード: append_exec_records
# ---------------------------------------------------------------------------

class TestAppendExecRecordsJsonl:
    def test_writes_valid_jsonl(self, pipeline_jsonl):
        """append_exec_records が valid JSON 行のみを書き込む。"""
        records = [
            {"uuid": UUID1, "command": "cat order.md | claude -p 'x'", "result_path": "jobs/r.md"},
        ]
        pipeline_jsonl.append_exec_records(records)

        lines = [l for l in pipeline_jsonl._exec_md_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["uuid"] == UUID1

    def test_no_comment_lines(self, pipeline_jsonl):
        """append_exec_records が # idempotency: 行を一切書かない。"""
        records = [
            {"uuid": UUID1, "command": "cmd", "idempotency_key": "scheduler:job:ts"},
        ]
        pipeline_jsonl.append_exec_records(records)

        content = pipeline_jsonl._exec_md_path.read_text()
        assert "# idempotency:" not in content

    def test_multiple_records_separate_lines(self, pipeline_jsonl):
        """複数レコードが個別の行として書き込まれ、各行が独立した valid JSON。"""
        records = [
            {"uuid": UUID1, "command": "cmd1"},
            {"uuid": UUID2, "command": "cmd2"},
        ]
        pipeline_jsonl.append_exec_records(records)

        lines = [l for l in pipeline_jsonl._exec_md_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 2
        assert json.loads(lines[0])["uuid"] == UUID1
        assert json.loads(lines[1])["uuid"] == UUID2

    def test_idempotency_key_embedded_in_record(self, pipeline_jsonl):
        """idempotency_key がレコードフィールドとして埋め込まれる。"""
        key = "scheduler:diary_review:2026-05-08T23:00:00+09:00"
        records = [{"uuid": UUID1, "command": "cmd", "idempotency_key": key}]
        pipeline_jsonl.append_exec_records(records)

        line = pipeline_jsonl._exec_md_path.read_text().strip()
        parsed = json.loads(line)
        assert parsed["idempotency_key"] == key

    def test_appends_to_existing_content(self, pipeline_jsonl):
        """既存 JSON 行を壊さずに追記する。"""
        existing = json.dumps({"uuid": UUID2, "command": "existing"})
        pipeline_jsonl._exec_md_path.write_text(existing + "\n", encoding="utf-8")

        pipeline_jsonl.append_exec_records([{"uuid": UUID1, "command": "new"}])

        lines = [l for l in pipeline_jsonl._exec_md_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 2
        for line in lines:
            json.loads(line)  # must all be valid JSON


# ---------------------------------------------------------------------------
# JSONL モード: check_idempotency
# ---------------------------------------------------------------------------

class TestCheckIdempotencyJsonlMode:
    def test_key_present_returns_false(self, pipeline_jsonl):
        """idempotency_key を含む JSON 行が存在するとき False を返す。"""
        key = "scheduler:diary_review:2026-05-08T23:00:00+09:00"
        record = json.dumps({"uuid": UUID1, "command": "cmd", "idempotency_key": key})
        pipeline_jsonl._exec_md_path.write_text(record + "\n", encoding="utf-8")

        assert pipeline_jsonl.check_idempotency(key) is False

    def test_key_absent_returns_true(self, pipeline_jsonl):
        """idempotency_key が存在しないとき True を返す。"""
        record = json.dumps({"uuid": UUID1, "command": "cmd"})
        pipeline_jsonl._exec_md_path.write_text(record + "\n", encoding="utf-8")

        assert pipeline_jsonl.check_idempotency("scheduler:new-job:ts") is True

    def test_empty_file_returns_true(self, pipeline_jsonl):
        """空ファイルのとき True を返す。"""
        assert pipeline_jsonl.check_idempotency("any-key") is True

    def test_nonexistent_file_returns_true(self, tmp_path):
        """ファイルが存在しないとき True を返す。"""
        state = PipelineState(
            state_dir=tmp_path / "state",
            exec_md_path=tmp_path / "nonexistent.jsonl",
        )
        assert state.check_idempotency("any-key") is True

    def test_text_comment_not_matched_in_jsonl_mode(self, pipeline_jsonl):
        """exec.jsonl に # idempotency: テキスト行があっても JSONL モードでは一致しない。

        テキスト形式の古い行が混入したシナリオ。JSONL モードは JSON フィールドのみを見る。
        """
        key = "scheduler:diary_review:2026-05-07T23:00:00+09:00"
        pipeline_jsonl._exec_md_path.write_text(
            f"# idempotency: {key}\n", encoding="utf-8"
        )
        # JSONL mode does NOT match text-format comment lines
        assert pipeline_jsonl.check_idempotency(key) is True

    def test_different_key_does_not_match(self, pipeline_jsonl):
        """異なる idempotency_key では一致しない。"""
        record = json.dumps({"uuid": UUID1, "command": "cmd", "idempotency_key": "other:key"})
        pipeline_jsonl._exec_md_path.write_text(record + "\n", encoding="utf-8")

        assert pipeline_jsonl.check_idempotency("scheduler:diary_review:ts") is True
