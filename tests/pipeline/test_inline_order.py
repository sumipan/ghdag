"""Tests for InlineOrderBuilder — AC-1 (Issue #678)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ghdag.pipeline.audit import AuditContext
from ghdag.pipeline.order import InlineOrderBuilder
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.workflow.schema import StepConfig


_TEST_AUDIT_CTX = AuditContext(source="test")


class TestInlineOrderBuilder:
    def test_normal_variable_expansion(self):
        builder = InlineOrderBuilder()
        result = builder.build_order("Hello ${name}", {"name": "world"})
        assert result == "Hello world"

    def test_no_variables(self):
        builder = InlineOrderBuilder()
        result = builder.build_order("固定テキスト", {})
        assert result == "固定テキスト"

    def test_multiple_variables(self):
        builder = InlineOrderBuilder()
        result = builder.build_order("${a} and ${b}", {"a": "X", "b": "Y"})
        assert result == "X and Y"

    def test_undefined_variable_raises_key_error(self):
        builder = InlineOrderBuilder()
        with pytest.raises(KeyError):
            builder.build_order("${missing}", {})

    def test_invalid_dollar_syntax_raises_value_error(self):
        builder = InlineOrderBuilder()
        with pytest.raises(ValueError):
            builder.build_order("${}", {})

    def test_protocol_conformance_with_llm_pipeline_api(self, tmp_path):
        """InlineOrderBuilder を order_builders に渡して submit が成功する。"""
        pipeline_state = MagicMock()
        pipeline_state.write_order_file.return_value = "ts-claude-order-uuid.md"
        default_builder = MagicMock()
        default_builder.build_order.return_value = "default order"

        api = LLMPipelineAPI(
            pipeline_state=pipeline_state,
            order_builder=default_builder,
            queue_dir="queue",
            order_builders={"scheduler": InlineOrderBuilder()},
        )
        steps = [StepConfig(template="プロンプト本文 ${issue_number}", model="claude-opus-4-6")]
        exec_lines = api.submit(
            steps,
            base_context={"workflow_name": "scheduler", "issue_number": "42"},
            audit_context=_TEST_AUDIT_CTX,
        )
        assert len(exec_lines) == 1
