"""Tests for InlineOrderBuilder — AC-1 (Issue #678)."""

from __future__ import annotations

from unittest.mock import MagicMock

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
        result = builder.build_order("fixed text", {})
        assert result == "fixed text"

    def test_multiple_variables(self):
        builder = InlineOrderBuilder()
        result = builder.build_order("${a} and ${b}", {"a": "X", "b": "Y"})
        assert result == "X and Y"

    def test_undefined_variable_passes_through(self):
        """Undefined variables remain as ${var} (safe_substitute behavior).

        For scheduler dynamic prompts (mltgnt skill actions, etc.) that include
        LLM-facing ${ENV_VAR} notation, leave undefined variables unsubstituted
        instead of raising so the scheduler does not die.
        """
        builder = InlineOrderBuilder()
        result = builder.build_order("${missing}", {})
        assert result == "${missing}"

    def test_partial_substitution(self):
        """Expand known variables only; leave unknown ones as ${var}."""
        builder = InlineOrderBuilder()
        result = builder.build_order("${p} ${q}", {"p": "1"})
        assert result == "1 ${q}"

    def test_skill_prompt_with_env_var_notation(self):
        """SKILL.md-style ${ENV_VAR} notation in a prompt must not fail.

        Regression: LLM-facing env notation with a CJK diary path segment via
        mltgnt skill action must not raise TemplateVariableError.
        """
        builder = InlineOrderBuilder()
        # Japanese text intentionally kept for CJK processing test
        prompt = "Target diary: ${NIKKI_ROOT}/日記/YYYY-MM-DD.md ($0)"
        result = builder.build_order(prompt, {"workflow_name": "scheduler"})
        # ${NIKKI_ROOT} and $0 remain unsubstituted without raising
        assert "${NIKKI_ROOT}" in result
        assert "$0" in result

    def test_malformed_placeholder_passes_through(self):
        """Malformed placeholder (${}) is left as-is by safe_substitute (no raise).

        Previously raised ValueError; on the scheduler dynamic-prompt path it is
        safer to pass the bad notation through to the LLM (which can judge from
        context) than to crash the scheduler thread.
        """
        builder = InlineOrderBuilder()
        result = builder.build_order("text ${} more", {})
        assert result == "text ${} more"

    def test_protocol_conformance_with_llm_pipeline_api(self, tmp_path):
        """Passing InlineOrderBuilder via order_builders lets submit succeed."""
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
        steps = [StepConfig(template="Prompt body ${issue_number}", model="claude-opus-4-6")]
        exec_lines = api.submit(
            steps,
            base_context={"workflow_name": "scheduler", "issue_number": "42"},
            audit_context=_TEST_AUDIT_CTX,
        )
        assert len(exec_lines) == 1
