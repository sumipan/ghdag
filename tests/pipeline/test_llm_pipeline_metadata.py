"""Tests for LLMPipelineAPI.submit() metadata argument (Issue #827).

Acceptance Criteria:
- AC-1 (正常系): metadata を渡すと exec.jsonl レコードの annotations に反映される
- AC-2 (正常系・複数ステップ): 複数ステップ全てに同一 annotations が付与される
- AC-3 (正常系・ラウンドトリップ): exec.jsonl → parse_jsonl() → Task.annotations が一致する
- AC-4 (後方互換): metadata 省略で annotations が {} のまま
- AC-5 (後方互換): idempotency_key と metadata を同時指定できる
- AC-6 (後方互換): テキストモードで metadata を渡してもエラーにならない
- AC-7 (境界値): metadata={} で annotations が {} のまま
- AC-8 (境界値): metadata=None で省略時と同一動作
"""

from __future__ import annotations

import json as _json
from unittest.mock import MagicMock

from ghdag.pipeline.audit import AuditContext
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.schema import StepConfig

_AUDIT = AuditContext(source="test")


def _make_api(
    queue_dir: str = "queue",
    *,
    jsonl_mode: bool = False,
) -> tuple[LLMPipelineAPI, MagicMock, MagicMock]:
    """LLMPipelineAPI with mocked PipelineState and OrderBuilder."""
    pipeline_state = MagicMock()
    pipeline_state.check_idempotency.return_value = True
    pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"
    pipeline_state._is_jsonl_mode = jsonl_mode
    order_builder = MagicMock()
    order_builder.build_order.return_value = "order content"
    api = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=order_builder,
        queue_dir=queue_dir,
    )
    return api, pipeline_state, order_builder


# ---------------------------------------------------------------------------
# AC-1: metadata を渡すと exec.jsonl レコードの annotations に反映される
# ---------------------------------------------------------------------------


class TestMetadataInAnnotations:
    def test_metadata_reflected_in_record_annotations(self):
        """submit(metadata={"k": "v"}) で annotations に {"k": "v"} が含まれる。"""
        api, _, _ = _make_api(jsonl_mode=True)
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_AUDIT, metadata={"k": "v"})

        record = _json.loads(exec_lines[0])
        assert record["annotations"].get("k") == "v"

    def test_metadata_channel_and_thread_ts(self):
        """channel_id と thread_ts を metadata で渡すと annotations に格納される。"""
        api, _, _ = _make_api(jsonl_mode=True)
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        metadata = {"channel_id": "C123", "thread_ts": "1234567890.000"}
        exec_lines = api.submit(steps, {}, audit_context=_AUDIT, metadata=metadata)

        record = _json.loads(exec_lines[0])
        assert record["annotations"]["channel_id"] == "C123"
        assert record["annotations"]["thread_ts"] == "1234567890.000"

    def test_submit_with_metadata_completes_without_error(self):
        """submit(metadata=...) が正常に完了する（例外なし）。"""
        api, _, _ = _make_api(jsonl_mode=True)
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        # Should not raise
        result = api.submit(steps, {}, audit_context=_AUDIT, metadata={"foo": "bar"})
        assert len(result) == 1


# ---------------------------------------------------------------------------
# AC-2: 複数ステップ全てに同一 annotations が付与される
# ---------------------------------------------------------------------------


class TestMetadataMultipleSteps:
    def test_all_steps_get_same_annotations(self):
        """複数ステップ投入時、全レコードに同一の annotations が付与される。"""
        api, _, _ = _make_api(jsonl_mode=True)
        steps = [
            StepConfig(id="s1", template="t1", model="m1"),
            StepConfig(id="s2", template="t2", model="m2", depends=["s1"]),
            StepConfig(id="s3", template="t3", model="m3", depends=["s2"]),
        ]
        metadata = {"channel_id": "C999", "thread_ts": "9999.000"}
        exec_lines = api.submit(steps, {}, audit_context=_AUDIT, metadata=metadata)

        assert len(exec_lines) == 3
        for line in exec_lines:
            record = _json.loads(line)
            assert record["annotations"]["channel_id"] == "C999"
            assert record["annotations"]["thread_ts"] == "9999.000"

    def test_two_steps_both_have_annotations(self):
        """2 ステップ（依存なし・依存あり）どちらにも annotations が付与される。"""
        api, _, _ = _make_api(jsonl_mode=True)
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6", depends=["p1"]),
        ]
        metadata = {"k": "v"}
        exec_lines = api.submit(steps, {}, audit_context=_AUDIT, metadata=metadata)

        for line in exec_lines:
            record = _json.loads(line)
            assert record["annotations"].get("k") == "v"


# ---------------------------------------------------------------------------
# AC-3: ラウンドトリップ検証 (submit → exec.jsonl → parse_jsonl() → Task.annotations)
# ---------------------------------------------------------------------------


class TestMetadataRoundTrip:
    def test_roundtrip_via_parse_jsonl(self):
        """submit() で metadata を渡し、exec.jsonl を parse_jsonl() で読むと
        Task.annotations に同一の値が取り出せる。"""
        from ghdag.dag.parser import parse_jsonl

        api, pipeline_state, _ = _make_api(jsonl_mode=True)

        # append_exec_records の呼び出しをキャプチャして exec.jsonl を模擬
        captured_records: list[dict] = []

        def capture_records(records, **kwargs):
            captured_records.extend(records)

        pipeline_state.append_exec_records.side_effect = capture_records

        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        metadata = {"channel_id": "C123", "thread_ts": "1234567890.000"}
        api.submit(steps, {}, audit_context=_AUDIT, metadata=metadata)

        assert len(captured_records) == 1
        # parse_jsonl は str を受け取る
        jsonl_content = _json.dumps(captured_records[0], ensure_ascii=False)
        tasks = parse_jsonl(jsonl_content)

        assert len(tasks) == 1
        assert tasks[0].annotations["channel_id"] == "C123"
        assert tasks[0].annotations["thread_ts"] == "1234567890.000"

    def test_roundtrip_multiple_steps(self):
        """複数ステップのラウンドトリップ: 全 Task.annotations が metadata と一致する。"""
        from ghdag.dag.parser import parse_jsonl

        api, pipeline_state, _ = _make_api(jsonl_mode=True)
        captured_records: list[dict] = []

        def capture_records(records, **kwargs):
            captured_records.extend(records)

        pipeline_state.append_exec_records.side_effect = capture_records

        steps = [
            StepConfig(id="p1", template="p1", model="m"),
            StepConfig(id="p2", template="p2", model="m", depends=["p1"]),
        ]
        metadata = {"slack_channel_id": "C001", "slack_thread_ts": "111.222"}
        api.submit(steps, {}, audit_context=_AUDIT, metadata=metadata)

        assert len(captured_records) == 2
        # parse_jsonl は str（改行区切りの JSONL テキスト）を受け取る
        jsonl_text = "\n".join(
            _json.dumps(r, ensure_ascii=False) for r in captured_records
        )
        tasks = parse_jsonl(jsonl_text)

        assert len(tasks) == 2
        for task in tasks:
            assert task.annotations["slack_channel_id"] == "C001"
            assert task.annotations["slack_thread_ts"] == "111.222"


# ---------------------------------------------------------------------------
# AC-4: metadata 省略で annotations が {} のまま（後方互換）
# ---------------------------------------------------------------------------


class TestMetadataBackwardCompat:
    def test_no_metadata_annotations_empty(self):
        """metadata を省略した場合、exec.jsonl の annotations は {} のまま。"""
        api, _, _ = _make_api(jsonl_mode=True)
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_AUDIT)

        record = _json.loads(exec_lines[0])
        assert record.get("annotations") == {}

    def test_no_metadata_does_not_break_existing_behavior(self):
        """metadata なしで既存のテスト項目（uuid, command, result_path）が正常。"""
        api, _, _ = _make_api(jsonl_mode=True)
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_AUDIT)

        record = _json.loads(exec_lines[0])
        assert "uuid" in record
        assert "command" in record
        assert "result_path" in record


# ---------------------------------------------------------------------------
# AC-5: idempotency_key と metadata を同時指定できる（後方互換）
# ---------------------------------------------------------------------------


class TestMetadataWithIdempotencyKey:
    def test_idempotency_key_and_metadata_together(self):
        """idempotency_key と metadata を同時に指定できる。"""
        api, _, _ = _make_api(jsonl_mode=True)
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(
            steps,
            {},
            idempotency_key="workflow:handler:42",
            audit_context=_AUDIT,
            metadata={"channel_id": "C123"},
        )

        record = _json.loads(exec_lines[0])
        assert record["idempotency_key"] == "workflow:handler:42"
        assert record["annotations"]["channel_id"] == "C123"


# ---------------------------------------------------------------------------
# AC-6: テキストモードで metadata を渡してもエラーにならない（後方互換）
# ---------------------------------------------------------------------------


class TestMetadataTextModeCompat:
    def test_text_mode_with_metadata_does_not_raise(self):
        """テキストモード（exec.md）で metadata を渡しても submit() がエラーにならない。"""
        api, pipeline_state, _ = _make_api(jsonl_mode=False)
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        # Should not raise
        exec_lines = api.submit(steps, {}, audit_context=_AUDIT, metadata={"channel_id": "C123"})
        assert len(exec_lines) >= 1

    def test_text_mode_metadata_ignored_silently(self):
        """テキストモードでは metadata が無視される（exec 行に annotations は出ない）。"""
        api, pipeline_state, _ = _make_api(jsonl_mode=False)
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_AUDIT, metadata={"channel_id": "C123"})

        # テキストモードの exec 行に "annotations" は含まれない
        for line in exec_lines:
            assert "annotations" not in line


# ---------------------------------------------------------------------------
# AC-7: metadata={} で annotations が {} のまま（境界値）
# ---------------------------------------------------------------------------


class TestMetadataEmptyDict:
    def test_empty_metadata_dict_annotations_stays_empty(self):
        """metadata={} を渡した場合、annotations は {} のまま。"""
        api, _, _ = _make_api(jsonl_mode=True)
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_AUDIT, metadata={})

        record = _json.loads(exec_lines[0])
        assert record.get("annotations") == {}


# ---------------------------------------------------------------------------
# AC-8: metadata=None で省略時と同一動作（境界値）
# ---------------------------------------------------------------------------


class TestMetadataExplicitNone:
    def test_explicit_none_metadata_same_as_omitted(self):
        """metadata=None を明示的に渡した場合、省略時と同一の動作になる。"""
        api, _, _ = _make_api(jsonl_mode=True)
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_AUDIT, metadata=None)

        record = _json.loads(exec_lines[0])
        # annotations は {} のまま（metadata なし相当）
        assert record.get("annotations") == {}
