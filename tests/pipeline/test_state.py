"""Tests for pipeline/state.py — append_exec audit integration (Issue #756)."""

from __future__ import annotations

import json

import pytest

from ghdag.pipeline.state import PipelineState


UUID1 = "38d6b791-1072-42f0-838d-45c7d10748ff"
UUID2 = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
UUID3 = "ccccdddd-eeee-ffff-0000-111122223333"


@pytest.fixture
def pipeline(tmp_path):
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.write_text("", encoding="utf-8")
    return PipelineState(state_dir=tmp_path / "state", exec_md_path=exec_jsonl)


@pytest.fixture
def pipeline_jsonl(tmp_path):
    exec_jsonl = tmp_path / "exec.jsonl"
    exec_jsonl.write_text("", encoding="utf-8")
    return PipelineState(state_dir=tmp_path / "state", exec_md_path=exec_jsonl)


class TestSubmitWithoutAuditContext:
    def test_ac7_submit_without_audit_context_raises_type_error(self):
        """AC7: 必須化 — LLMPipelineAPI.submit() の audit_context 省略で TypeError。"""
        from unittest.mock import MagicMock

        from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
        from ghdag.workflow.schema import StepConfig

        pipeline_state = MagicMock()
        pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"
        order_builder = MagicMock()
        order_builder.build_order.return_value = "order content"
        api = LLMPipelineAPI(
            pipeline_state=pipeline_state,
            order_builder=order_builder,
        )
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        with pytest.raises(TypeError):
            api.submit(steps, {})


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

        lines = [ln for ln in pipeline_jsonl._exec_md_path.read_text().splitlines() if ln.strip()]
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

        lines = [ln for ln in pipeline_jsonl._exec_md_path.read_text().splitlines() if ln.strip()]
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

        lines = [ln for ln in pipeline_jsonl._exec_md_path.read_text().splitlines() if ln.strip()]
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

    def test_invalid_json_line_skipped(self, pipeline_jsonl):
        """JSON パース失敗行はスキップし正常行のみ判定する（A1-2）。"""
        key = "scheduler:diary_review:2026-05-08T23:00:00+09:00"
        valid_record = json.dumps({"uuid": UUID1, "command": "cmd", "idempotency_key": key})
        pipeline_jsonl._exec_md_path.write_text(
            "this-is-broken-json\n"
            + valid_record + "\n",
            encoding="utf-8",
        )
        assert pipeline_jsonl.check_idempotency(key) is False

    def test_record_without_idempotency_key_does_not_match(self, pipeline_jsonl):
        """idempotency_key フィールドを持たないレコードはマッチしない（A1-2）。"""
        key = "some:key:value"
        record = json.dumps({"uuid": UUID1, "command": "cmd"})
        pipeline_jsonl._exec_md_path.write_text(record + "\n", encoding="utf-8")

        assert pipeline_jsonl.check_idempotency(key) is True


# ---------------------------------------------------------------------------
# JSONL モード: remove_idempotency_matching (AC2)
# ---------------------------------------------------------------------------


class TestRemoveIdempotencyMatchingJsonl:
    @pytest.fixture
    def jsonl_state(self, tmp_path):
        exec_jsonl = tmp_path / "exec.jsonl"
        exec_jsonl.write_text("", encoding="utf-8")
        return PipelineState(state_dir=tmp_path / "state", exec_md_path=exec_jsonl)

    def test_removes_two_matching_records(self, jsonl_state):
        """AC2-1: workflow_name と issue_number にマッチする 2 レコードを削除、返り値 2。"""
        jsonl_state._exec_md_path.write_text(
            json.dumps({"uuid": UUID1, "idempotency_key": "wf:handler_a:42"}) + "\n"
            + json.dumps({"uuid": UUID2, "idempotency_key": "wf:handler_b:42"}) + "\n",
            encoding="utf-8",
        )
        removed = jsonl_state.remove_idempotency_matching("wf", 42)
        assert removed == 2
        content = jsonl_state._exec_md_path.read_text()
        assert "handler_a" not in content
        assert "handler_b" not in content

    def test_no_match_returns_zero_and_file_unchanged(self, jsonl_state):
        """AC2-2: マッチなし（issue_number 違い）→ 返り値 0、ファイル変更なし。"""
        original = json.dumps({"uuid": UUID1, "idempotency_key": "wf:handler_a:99"}) + "\n"
        jsonl_state._exec_md_path.write_text(original, encoding="utf-8")
        removed = jsonl_state.remove_idempotency_matching("wf", 42)
        assert removed == 0
        assert jsonl_state._exec_md_path.read_text() == original

    def test_keyless_and_non_matching_records_preserved(self, jsonl_state):
        """AC2-3: idempotency_key なし・非マッチレコードは残る。"""
        jsonl_state._exec_md_path.write_text(
            json.dumps({"uuid": UUID1, "idempotency_key": "wf:handler_a:42"}) + "\n"
            + json.dumps({"uuid": UUID2, "idempotency_key": "wf:handler_b:99"}) + "\n"
            + json.dumps({"uuid": UUID3, "command": "echo hi"}) + "\n",
            encoding="utf-8",
        )
        removed = jsonl_state.remove_idempotency_matching("wf", 42)
        assert removed == 1
        content = jsonl_state._exec_md_path.read_text()
        assert "handler_a" not in content
        assert "handler_b" in content
        assert UUID3 in content

    def test_file_not_exists_returns_zero(self, tmp_path):
        """AC2-4: ファイル不在 → 返り値 0、エラーなし。"""
        state = PipelineState(
            state_dir=tmp_path / "state",
            exec_md_path=tmp_path / "nonexistent.jsonl",
        )
        assert state.remove_idempotency_matching("wf", 42) == 0

    def test_empty_lines_preserved(self, jsonl_state):
        """AC2-5: 空行は保持される。"""
        jsonl_state._exec_md_path.write_text(
            json.dumps({"uuid": UUID1, "idempotency_key": "wf:handler_a:42"}) + "\n"
            + "\n"
            + json.dumps({"uuid": UUID2, "command": "cmd"}) + "\n",
            encoding="utf-8",
        )
        jsonl_state.remove_idempotency_matching("wf", 42)
        remaining = jsonl_state._exec_md_path.read_text()
        assert "" in remaining.splitlines()
        assert "handler_a" not in remaining
        assert UUID2 in remaining
