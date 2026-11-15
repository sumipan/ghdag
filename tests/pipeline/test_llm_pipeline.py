"""Tests for ghdag.pipeline.llm_pipeline — LLMPipelineAPI (Issue #203)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from ghdag_pipeline_conftest_helpers import _TEST_AUDIT_CTX, _make_api, _track_build_order

from ghdag.core.models.workflow import StepConfig
from ghdag.pipeline.llm_pipeline import DependencyError, LLMPipelineAPI, validate_depends


class TestAC3DependsValidation:

    def test_circular_dependency_a_b_a_raises_value_error(self):
        """A→B→A circular dependency → ValueError containing 'circular dependency'"""
        api, _, _ = _make_api()
        steps = [
            StepConfig(id="a", template="t", model="m", depends=["b"]),
            StepConfig(id="b", template="t", model="m", depends=["a"]),
        ]
        with pytest.raises(DependencyError, match="circular dependency"):
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)
_P1_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_P2_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_P3_UUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_TS = "20260523120000"


def _make_api_with_tmpdir(tmp_path):
    """LLMPipelineAPI that uses a real tmp_path as queue_dir."""
    pipeline_state = MagicMock()
    pipeline_state.check_idempotency.return_value = True
    pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"
    order_builder = MagicMock()
    order_builder.build_order.return_value = "order content"
    api = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=order_builder,
        queue_dir=str(tmp_path),
    )
    return api, pipeline_state, order_builder


class TestResultContentInjection:
    """Issue #1014: ${dep_id_result_content} context injection."""

    def test_submit_injects_multiple_dep_result_contents(self, tmp_path):
        """AC4: p3 depends on both p1 and p2 → both result_content values are injected."""
        content_p1 = "p1 result"
        content_p2 = "p2 result"
        (tmp_path / f"{_TS}-claude-result-{_P1_UUID}.md").write_text(content_p1, encoding="utf-8")
        (tmp_path / f"{_TS}-claude-result-{_P2_UUID}.md").write_text(content_p2, encoding="utf-8")

        api, _, order_builder = _make_api_with_tmpdir(tmp_path)
        captured = _track_build_order(order_builder)
        steps = [
            StepConfig(id="p1", template="p1", model="claude-sonnet-4-6"),
            StepConfig(id="p2", template="p2", model="claude-sonnet-4-6"),
            StepConfig(id="p3", template="p3", model="claude-sonnet-4-6", depends=["p1", "p2"]),
        ]
        with patch("ghdag.pipeline.llm_pipeline.uuid.uuid4", side_effect=[_P1_UUID, _P2_UUID, _P3_UUID]), \
             patch("ghdag.pipeline.llm_pipeline.file_ts", return_value=_TS):
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        p3_ctx = captured[2][1]
        assert p3_ctx["p1_result_content"] == content_p1
        assert p3_ctx["p2_result_content"] == content_p2


_A_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_B_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_FORWARD_TS = "20260523120000"


class TestEnvPassthroughInAnnotations:
    def test_submit_writes_env_passthrough_to_annotations(self):
        """Non-empty StepConfig.env_passthrough is copied to record annotations."""
        import json as _json

        api, _, _ = _make_api()
        steps = [
            StepConfig(
                template="brushup",
                model="claude-opus-4-6",
                env_passthrough=["MY_TOKEN"],
            ),
        ]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert record["annotations"]["env_passthrough"] == ["MY_TOKEN"]


class TestValidateDependsPublic:
    """Issue #6167 — validate_depends is importable and rejects unknown deps."""

    def test_import_validate_depends(self):
        assert callable(validate_depends)


class TestUnknownEngine:
    def test_unknown_engine_raises_engine_model_error(self):
        """#6142: unknown engine → EngineModelError(ENGINE_UNKNOWN), not KeyError."""
        from ghdag.llm.engines import EngineModelError

        api, _, _ = _make_api()
        steps = [
            StepConfig(template="t", model="claude-opus-4-6", engine="nonexistent"),
        ]
        with pytest.raises(EngineModelError) as exc_info:
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)
        assert exc_info.value.code == "ENGINE_UNKNOWN"
        assert "claude" in (exc_info.value.hint or "")


class TestSubmitFileTimestampTimezone:
    """Refs sumipan/nexus#6872."""

    def test_submit_file_ts_uses_given_timezone(self, tmp_path):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from ghdag.io.audit import file_ts as audit_file_ts
        from ghdag.pipeline.order import InlineOrderBuilder
        from ghdag.pipeline.state import PipelineState

        queue = tmp_path / "queue"
        queue.mkdir()
        exec_jsonl = tmp_path / "exec.jsonl"
        exec_jsonl.write_text("", encoding="utf-8")
        state = PipelineState(state_dir=tmp_path / "state", exec_jsonl_path=exec_jsonl)
        fixed = datetime(2024, 7, 4, 16, 0, 0, tzinfo=ZoneInfo("UTC"))
        expected = audit_file_ts("America/New_York", now=fixed)

        def _file_ts(tz_name: str | None) -> str:
            return audit_file_ts(tz_name, now=fixed)

        api = LLMPipelineAPI(
            state,
            InlineOrderBuilder(),
            queue_dir=str(queue),
            timezone="America/New_York",
        )
        steps = [StepConfig(template="run", engine="claude", model="claude-sonnet-4-6")]
        with patch("ghdag.pipeline.llm_pipeline.file_ts", side_effect=_file_ts):
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)
        orders = list(queue.glob("*-claude-order-*.md"))
        assert len(orders) == 1
        assert orders[0].name.startswith(expected)

    def test_submit_file_ts_defaults_to_machine_timezone(self, tmp_path, monkeypatch):
        import os
        import time
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from ghdag.io.audit import file_ts as audit_file_ts
        from ghdag.pipeline.order import InlineOrderBuilder
        from ghdag.pipeline.state import PipelineState

        old_tz = os.environ.get("TZ")
        monkeypatch.setenv("TZ", "Pacific/Auckland")
        time.tzset()
        try:
            queue = tmp_path / "queue"
            queue.mkdir()
            exec_jsonl = tmp_path / "exec.jsonl"
            exec_jsonl.write_text("", encoding="utf-8")
            state = PipelineState(state_dir=tmp_path / "state", exec_jsonl_path=exec_jsonl)
            fixed = datetime(2024, 7, 4, 16, 0, 0, tzinfo=ZoneInfo("UTC"))
            expected = audit_file_ts(None, now=fixed)

            def _file_ts(tz_name: str | None) -> str:
                return audit_file_ts(tz_name, now=fixed)

            api = LLMPipelineAPI(state, InlineOrderBuilder(), queue_dir=str(queue))
            steps = [StepConfig(template="run", engine="claude", model="claude-sonnet-4-6")]
            with patch("ghdag.pipeline.llm_pipeline.file_ts", side_effect=_file_ts):
                api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)
            orders = list(queue.glob("*-claude-order-*.md"))
            assert len(orders) == 1
            assert orders[0].name.startswith(expected)
        finally:
            if old_tz is None:
                monkeypatch.delenv("TZ", raising=False)
            else:
                monkeypatch.setenv("TZ", old_tz)
            time.tzset()
