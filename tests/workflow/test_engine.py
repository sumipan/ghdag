"""Tests for ghdag.workflow.engine — EngineAdapter, ClaudeAdapter, GeminiAdapter, get_adapter."""

from __future__ import annotations

import pytest

from ghdag.workflow.engine import (
    ClaudeAdapter,
    GeminiAdapter,
    get_adapter,
    register_adapter,
    _ADAPTERS,
)


# ---------------------------------------------------------------------------
# ClaudeAdapter
# ---------------------------------------------------------------------------

class TestClaudeAdapter:
    def setup_method(self):
        self.adapter = ClaudeAdapter()
        self.base_kwargs = dict(
            order_path="queue/ts-claude-order-abc123.md",
            result_path="queue/ts-claude-result-abc123.md",
            prompt="受け取った内容を実行して",
        )

    def test_name(self):
        assert self.adapter.name == "claude"

    def test_full_line_with_model_no_depends(self):
        line = self.adapter.build_exec_line(
            uuid="abc123",
            model="claude-opus-4-6",
            depends=[],
            **self.base_kwargs,
        )
        expected = (
            "abc123: cat queue/ts-claude-order-abc123.md"
            " | claude -p '受け取った内容を実行して' --model 'claude-opus-4-6'"
            " --dangerously-skip-permissions"
            " | tee -a queue/ts-claude-result-abc123.md"
        )
        assert line == expected

    def test_no_model_flag_when_model_is_none(self):
        line = self.adapter.build_exec_line(
            uuid="def456",
            model=None,
            depends=["abc123"],
            **self.base_kwargs,
        )
        assert "--model" not in line
        assert "--dangerously-skip-permissions" in line
        assert "[depends:abc123]" in line

    def test_single_depends(self):
        line = self.adapter.build_exec_line(
            uuid="def456",
            model=None,
            depends=["abc123"],
            **self.base_kwargs,
        )
        assert line.startswith("def456[depends:abc123]:")

    def test_multiple_depends(self):
        line = self.adapter.build_exec_line(
            uuid="ghi789",
            model="claude-sonnet-4-6",
            depends=["abc123", "def456"],
            **self.base_kwargs,
        )
        assert line.startswith("ghi789[depends:abc123,def456]:")

    def test_no_depends_no_bracket(self):
        line = self.adapter.build_exec_line(
            uuid="abc123",
            model="claude-opus-4-6",
            depends=[],
            **self.base_kwargs,
        )
        assert "[depends:" not in line

    def test_tee_result_path(self):
        line = self.adapter.build_exec_line(
            uuid="abc123",
            model=None,
            depends=[],
            **self.base_kwargs,
        )
        assert "| tee -a queue/ts-claude-result-abc123.md" in line

    def test_dangerously_skip_permissions_always_present(self):
        line = self.adapter.build_exec_line(
            uuid="x",
            model=None,
            depends=[],
            **self.base_kwargs,
        )
        assert "--dangerously-skip-permissions" in line


# ---------------------------------------------------------------------------
# GeminiAdapter
# ---------------------------------------------------------------------------

class TestGeminiAdapter:
    def setup_method(self):
        self.adapter = GeminiAdapter()
        self.base_kwargs = dict(
            order_path="queue/ts-gemini-order-abc123.md",
            result_path="queue/ts-gemini-result-abc123.md",
            prompt="受け取った内容を実行して",
        )

    def test_name(self):
        assert self.adapter.name == "gemini"

    def test_with_model_no_depends(self):
        line = self.adapter.build_exec_line(
            uuid="abc123",
            model="flash",
            depends=[],
            **self.base_kwargs,
        )
        assert "abc123:" in line
        assert "gemini -p" in line
        assert "-m flash" in line
        assert "--approval-mode yolo" in line
        assert "[depends:" not in line

    def test_no_model_flag_when_model_is_none(self):
        line = self.adapter.build_exec_line(
            uuid="def456",
            model=None,
            depends=[],
            **self.base_kwargs,
        )
        assert "-m " not in line
        assert "--approval-mode yolo" in line

    def test_single_depends(self):
        line = self.adapter.build_exec_line(
            uuid="def456",
            model=None,
            depends=["abc123"],
            **self.base_kwargs,
        )
        assert line.startswith("def456[depends:abc123]:")

    def test_tee_result_path(self):
        line = self.adapter.build_exec_line(
            uuid="abc123",
            model=None,
            depends=[],
            **self.base_kwargs,
        )
        assert "| tee -a queue/ts-gemini-result-abc123.md" in line

    def test_approval_mode_yolo_always_present(self):
        line = self.adapter.build_exec_line(
            uuid="x",
            model=None,
            depends=[],
            **self.base_kwargs,
        )
        assert "--approval-mode yolo" in line


# ---------------------------------------------------------------------------
# get_adapter / register_adapter
# ---------------------------------------------------------------------------

class TestGetAdapter:
    def test_get_claude_returns_claude_adapter(self):
        adapter = get_adapter("claude")
        assert isinstance(adapter, ClaudeAdapter)

    def test_get_gemini_returns_gemini_adapter(self):
        adapter = get_adapter("gemini")
        assert isinstance(adapter, GeminiAdapter)

    def test_unknown_engine_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown engine"):
            get_adapter("unknown")

    def test_error_message_contains_available_engines(self):
        with pytest.raises(ValueError) as exc_info:
            get_adapter("unknown")
        msg = str(exc_info.value)
        assert "claude" in msg or "gemini" in msg

    def test_register_custom_adapter(self):
        class TestAdapter:
            name = "_test_engine_"
            def build_exec_line(self, **kwargs):
                return "test"

        original_adapters = dict(_ADAPTERS)
        try:
            register_adapter(TestAdapter())
            adapter = get_adapter("_test_engine_")
            assert isinstance(adapter, TestAdapter)
        finally:
            # cleanup
            _ADAPTERS.clear()
            _ADAPTERS.update(original_adapters)
