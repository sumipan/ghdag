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

    def test_undefined_variable_raises_value_error(self):
        builder = InlineOrderBuilder()
        with pytest.raises(ValueError) as exc_info:
            builder.build_order("${missing}", {})
        assert "テンプレート展開エラー" in str(exc_info.value)

    def test_invalid_dollar_syntax_raises_value_error(self):
        builder = InlineOrderBuilder()
        with pytest.raises(ValueError) as exc_info:
            builder.build_order("${}", {})
        assert "テンプレート展開エラー" in str(exc_info.value)

    def test_t4_missing_var_shows_available_keys(self):
        """T4: 不足変数+利用可能キーが ValueError メッセージに含まれる"""
        builder = InlineOrderBuilder()
        with pytest.raises(ValueError) as exc_info:
            builder.build_order("${p} ${q}", {"p": "1"})

        msg = str(exc_info.value)
        assert "q" in msg
        assert "p" in msg  # 利用可能キーに含まれる

    def test_t5_invalid_syntax_wrapped_as_value_error(self):
        """T5: 不正 $ 構文が ValueError としてラッピングされる"""
        builder = InlineOrderBuilder()
        with pytest.raises(ValueError) as exc_info:
            builder.build_order("${}", {})

        msg = str(exc_info.value)
        assert "テンプレート展開エラー" in msg
        assert exc_info.value.__cause__ is not None

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
