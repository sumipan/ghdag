"""Tests for ghdag.llm — one-shot LLM call interface"""

from __future__ import annotations

import pytest

from ghdag.core.command import render_exec_command
from ghdag.core.engine_spec import ENGINE_SPECS, EngineSpec


class TestEngineSpec:
    def test_engine_specs_has_all_engines(self):
        assert "claude" in ENGINE_SPECS
        assert "gemini" in ENGINE_SPECS
        assert "cursor" in ENGINE_SPECS
        assert "shell" in ENGINE_SPECS

    def test_engine_spec_frozen(self):
        import dataclasses
        spec = ENGINE_SPECS["claude"]
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            spec.name = "other"  # type: ignore

    def test_claude_spec_cli(self):
        assert ENGINE_SPECS["claude"].cli == "claude"

    def test_gemini_spec_cli(self):
        assert ENGINE_SPECS["gemini"].cli == "gemini"

    def test_cursor_spec_cli(self):
        assert ENGINE_SPECS["cursor"].cli == "cursor-agent"

    def test_shell_spec_cli(self):
        assert ENGINE_SPECS["shell"].cli == "bash"

    def test_engine_specs_exported_from_ghdag_llm(self):
        assert EngineSpec is not None
        assert ENGINE_SPECS is not None

    def test_cursor_danger_flag_position_leading(self):
        assert ENGINE_SPECS["cursor"].danger_flag_position == "leading"

    def test_claude_danger_flag_position_trailing(self):
        assert ENGINE_SPECS["claude"].danger_flag_position == "trailing"

    def test_gemini_model_flag_is_double_dash(self):
        assert ENGINE_SPECS["gemini"].model_flag == "--model"

    def test_gemini_no_danger_flag(self):
        assert ENGINE_SPECS["gemini"].danger_flag is None

    def test_shell_no_model_flag(self):
        assert ENGINE_SPECS["shell"].model_flag is None

    def test_shell_extra_args_pipefail(self):
        assert "-o" in ENGINE_SPECS["shell"].extra_args
        assert "pipefail" in ENGINE_SPECS["shell"].extra_args


class TestRenderExecCommand:

    def test_claude_with_model(self):
        spec = ENGINE_SPECS["claude"]
        cmd = render_exec_command(spec, order_path="queue/order.md", model="claude-opus-4-6")
        assert cmd == "claude -p --model 'claude-opus-4-6' --output-format stream-json --verbose --dangerously-skip-permissions < queue/order.md"

    def test_claude_without_model(self):
        spec = ENGINE_SPECS["claude"]
        cmd = render_exec_command(spec, order_path="queue/order.md", model=None)
        assert cmd == "claude -p --output-format stream-json --verbose --dangerously-skip-permissions < queue/order.md"

    def test_gemini_with_model(self):
        spec = ENGINE_SPECS["gemini"]
        cmd = render_exec_command(spec, order_path="queue/order.md", model="gemini-2.5-flash")
        assert cmd == "gemini -p --model 'gemini-2.5-flash' --approval-mode yolo < queue/order.md"

    def test_shell_command(self):
        spec = ENGINE_SPECS["shell"]
        cmd = render_exec_command(spec, order_path="queue/order.sh", model=None)
        assert cmd == "bash -o pipefail queue/order.sh"
