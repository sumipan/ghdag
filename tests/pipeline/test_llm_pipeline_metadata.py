"""Tests for ghdag.pipeline.llm_pipeline — LLMPipelineAPI (Issue #203)."""

from __future__ import annotations

from ghdag_pipeline_conftest_helpers import _TEST_AUDIT_CTX, _make_api

from ghdag.workflow.schema import StepConfig


class TestMetadataInAnnotations:

    def test_metadata_reflected_in_record_annotations(self):
        """submit(metadata={"k": "v"}) includes {"k": "v"} in annotations."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template='brushup', model='claude-opus-4-6')]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX, metadata={'k': 'v'})
        record = _json.loads(exec_lines[0])
        assert record['annotations'].get('k') == 'v'

    def test_metadata_channel_and_thread_ts(self):
        """Passing channel_id and thread_ts via metadata stores them in annotations."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template='brushup', model='claude-opus-4-6')]
        metadata = {'channel_id': 'C123', 'thread_ts': '1234567890.000'}
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX, metadata=metadata)
        record = _json.loads(exec_lines[0])
        assert record['annotations']['channel_id'] == 'C123'
        assert record['annotations']['thread_ts'] == '1234567890.000'

class TestMetadataMultipleSteps:

    def test_all_steps_get_same_annotations(self):
        """When submitting multiple steps, every record gets the same annotations."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(id='s1', template='t1', model='m1'), StepConfig(id='s2', template='t2', model='m2', depends=['s1']), StepConfig(id='s3', template='t3', model='m3', depends=['s2'])]
        metadata = {'channel_id': 'C999', 'thread_ts': '9999.000'}
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX, metadata=metadata)
        assert len(exec_lines) == 3
        for line in exec_lines:
            record = _json.loads(line)
            assert record['annotations']['channel_id'] == 'C999'
            assert record['annotations']['thread_ts'] == '9999.000'

class TestMetadataRoundTrip:
    def test_roundtrip_via_parse_jsonl(self):
        """submit() with metadata: exec.jsonl → parse_jsonl() → Task.annotations match."""
        import json as _json

        from ghdag.dag.parser import parse_jsonl

        api, pipeline_state, _ = _make_api()
        captured_records: list[dict] = []

        def capture_records(records, **kwargs):
            captured_records.extend(records)

        pipeline_state.append_exec_records.side_effect = capture_records

        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        metadata = {"channel_id": "C123", "thread_ts": "1234567890.000"}
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX, metadata=metadata)

        assert len(captured_records) == 1
        jsonl_content = _json.dumps(captured_records[0], ensure_ascii=False)
        tasks = parse_jsonl(jsonl_content)

        assert len(tasks) == 1
        assert tasks[0].annotations["channel_id"] == "C123"
        assert tasks[0].annotations["thread_ts"] == "1234567890.000"

    def test_roundtrip_multiple_steps(self):
        """Multi-step round-trip: every Task.annotations matches metadata."""
        import json as _json

        from ghdag.dag.parser import parse_jsonl

        api, pipeline_state, _ = _make_api()
        captured_records: list[dict] = []

        def capture_records(records, **kwargs):
            captured_records.extend(records)

        pipeline_state.append_exec_records.side_effect = capture_records

        steps = [
            StepConfig(id="p1", template="p1", model="m"),
            StepConfig(id="p2", template="p2", model="m", depends=["p1"]),
        ]
        metadata = {"slack_channel_id": "C001", "slack_thread_ts": "111.222"}
        api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX, metadata=metadata)

        assert len(captured_records) == 2
        jsonl_text = "\n".join(
            _json.dumps(r, ensure_ascii=False) for r in captured_records
        )
        tasks = parse_jsonl(jsonl_text)

        assert len(tasks) == 2
        for task in tasks:
            assert task.annotations["slack_channel_id"] == "C001"
            assert task.annotations["slack_thread_ts"] == "111.222"
class TestMetadataBackwardCompat:

    def test_no_metadata_annotations_empty(self):
        """When metadata is omitted, gemini has no metadata-derived annotation keys (safe default only)."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template='brushup', model='gemini-2.5-pro', engine='gemini')]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)
        record = _json.loads(exec_lines[0])
        annotations = record.get('annotations', {})
        assert annotations.get('safe_default_applied') is True
        assert annotations.get('safe_default_preset') == 'text_only'
        assert 'step_name' in annotations
        assert len(annotations) == 3

class TestMetadataWithIdempotencyKey:
    def test_idempotency_key_and_metadata_together(self):
        """idempotency_key and metadata can be specified together."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(
            steps,
            {},
            idempotency_key="workflow:handler:42",
            audit_context=_TEST_AUDIT_CTX,
            metadata={"channel_id": "C123"},
        )

        record = _json.loads(exec_lines[0])
        assert record["idempotency_key"] == "workflow:handler:42"
        assert record["annotations"]["channel_id"] == "C123"


class TestMetadataEmptyDict:
    def test_empty_metadata_dict_annotations_stays_empty(self):
        """With metadata={}, gemini has no metadata-derived annotation keys (safe default only)."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="gemini-2.5-pro", engine="gemini")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX, metadata={})

        record = _json.loads(exec_lines[0])
        annotations = record.get("annotations", {})
        assert annotations.get("safe_default_applied") is True
        assert annotations.get("safe_default_preset") == "text_only"
        assert "step_name" in annotations
        assert len(annotations) == 3


class TestMetadataExplicitNone:
    def test_explicit_none_metadata_same_as_omitted(self):
        """With metadata=None explicitly, gemini matches the omitted case (safe default only)."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="gemini-2.5-pro", engine="gemini")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX, metadata=None)

        record = _json.loads(exec_lines[0])
        annotations = record.get("annotations", {})
        assert annotations.get("safe_default_applied") is True
        assert annotations.get("safe_default_preset") == "text_only"
        assert "step_name" in annotations
        assert len(annotations) == 3
