"""Tests for InlineOrderBuilder — AC-1 (Issue #678)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ghdag.pipeline.audit import AuditContext
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.pipeline.order import InlineOrderBuilder
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

    def test_undefined_variable_passes_through(self):
        """未定義変数は ${var} のまま残す（safe_substitute 挙動）。

        scheduler の動的プロンプト（mltgnt skill action 等）で LLM 向けの
        ${ENV_VAR} 表記が含まれた場合に scheduler が死なないよう、未定義変数を
        raise せずそのまま通す仕様。
        """
        builder = InlineOrderBuilder()
        result = builder.build_order("${missing}", {})
        assert result == "${missing}"

    def test_partial_substitution(self):
        """既知変数だけ展開し、未知は ${var} のまま残す。"""
        builder = InlineOrderBuilder()
        result = builder.build_order("${p} ${q}", {"p": "1"})
        assert result == "1 ${q}"

    def test_skill_prompt_with_env_var_notation(self):
        """SKILL.md 風の ${ENV_VAR} 記法を含むプロンプトが落ちない。

        mltgnt skill action 経由で `${NIKKI_ROOT}/日記/...` のような LLM 向け
        環境変数表記が prompt に含まれても TemplateVariableError を raise しない
        ことの回帰テスト。
        """
        builder = InlineOrderBuilder()
        prompt = "対象日記: ${NIKKI_ROOT}/日記/YYYY-MM-DD.md ($0)"
        result = builder.build_order(prompt, {"workflow_name": "scheduler"})
        # ${NIKKI_ROOT} と $0 が未定義でも raise されずに通る
        assert "${NIKKI_ROOT}" in result
        assert "$0" in result

    def test_malformed_placeholder_passes_through(self):
        """malformed placeholder (${}) も safe_substitute では raise せず残る。

        従来は ValueError を raise していたが、scheduler の動的プロンプト経路で
        scheduler スレッドが落ちるよりも、不正記法をそのまま LLM に渡したほうが
        運用上安全（LLM が文脈で判断できる）。
        """
        builder = InlineOrderBuilder()
        result = builder.build_order("text ${} more", {})
        assert result == "text ${} more"

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
