"""Tests for ghdag.pipeline.llm_pipeline — TaskQueue (Issue #203)."""

from __future__ import annotations

from ghdag_pipeline_conftest_helpers import _TEST_AUDIT_CTX, _make_api

from ghdag.core.models.workflow import StepConfig


class TestMetadataInAnnotations:

    def test_metadata_reflected_in_record_annotations(self):
        """submit(metadata={"k": "v"}) includes {"k": "v"} in annotations."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.enqueue(steps, {}, audit_context=_TEST_AUDIT_CTX, metadata={"k": "v"})

        record = _json.loads(exec_lines[0])
        assert record["annotations"].get("k") == "v"
