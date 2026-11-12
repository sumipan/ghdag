"""Tests for ghdag.llm — one-shot LLM call interface"""

from __future__ import annotations

import pytest

from ghdag.core.command import render_exec_command
from ghdag.core.engine_spec import ENGINE_SPECS, EngineSpec
from ghdag.llm import Permission


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

    def test_cursor_with_model(self):
        spec = ENGINE_SPECS["cursor"]
        cmd = render_exec_command(spec, order_path="queue/order.md", model="auto")
        assert cmd == (
            "cursor-agent --model 'auto' -p --force --output-format stream-json "
            "--stream-partial-output < queue/order.md"
        )

    def test_cursor_without_model(self):
        spec = ENGINE_SPECS["cursor"]
        cmd = render_exec_command(spec, order_path="queue/order.md", model=None)
        assert cmd == (
            "cursor-agent -p --force --output-format stream-json "
            "--stream-partial-output < queue/order.md"
        )

    def test_shell_command(self):
        spec = ENGINE_SPECS["shell"]
        cmd = render_exec_command(spec, order_path="queue/order.sh", model=None)
        assert cmd == "bash -o pipefail queue/order.sh"

    def test_prompt_ignored_for_flag_only(self):
        """FLAG_ONLY does not put the prompt string on argv"""
        spec = ENGINE_SPECS["claude"]
        cmd = render_exec_command(
            spec, order_path="jobs/o.md", model="claude-sonnet-4-6", prompt="any-string",
        )
        assert "-p '" not in cmd
        assert "any-string" not in cmd

    def test_no_engine_embeds_prompt_value_in_argv(self):
        """Generated commands for all ENGINE_SPECS must not contain `-p '`"""
        for name, spec in ENGINE_SPECS.items():
            cmd = render_exec_command(
                spec, order_path="jobs/o.md", model=spec.default_model, prompt="hello",
            )
            assert "-p '" not in cmd, f"{name}: {cmd}"


class TestRenderExecCommandCapabilities:
    def test_render_exec_claude_text_only_adds_permission_and_disallowed_tools(self):
        """AC1: permission=TEXT_ONLY → --permission-mode default --disallowed-tools ...; no --dangerously-skip-permissions"""
        from ghdag.core.capabilities import TEXT_ONLY
        spec = ENGINE_SPECS["claude"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", model="claude-opus-4-6",
            permission=TEXT_ONLY,
        )
        assert "--permission-mode" in cmd
        assert "default" in cmd
        assert "--disallowed-tools" in cmd
        for tool in ("Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch"):
            assert tool in cmd
        assert "--dangerously-skip-permissions" not in cmd

    def test_render_exec_claude_dangerous_uses_bypass_permissions(self):
        """AC2: permission=DANGEROUS_FULL_ACCESS → --permission-mode bypassPermissions"""
        from ghdag.core.capabilities import DANGEROUS_FULL_ACCESS
        spec = ENGINE_SPECS["claude"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", model=None,
            permission=DANGEROUS_FULL_ACCESS,
        )
        assert "--permission-mode" in cmd
        assert "bypassPermissions" in cmd
        assert "--dangerously-skip-permissions" not in cmd

    def test_render_exec_cursor_text_only_omits_force(self):
        """AC3: cursor + permission=TEXT_ONLY → does not include --force"""
        from ghdag.core.capabilities import TEXT_ONLY
        spec = ENGINE_SPECS["cursor"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", model="auto",
            permission=TEXT_ONLY,
        )
        assert "--force" not in cmd

    def test_render_exec_claude_default_keeps_dangerously_skip_permissions(self):
        """AC8: permission=None (default) → --dangerously-skip-permissions as before"""
        spec = ENGINE_SPECS["claude"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", model="claude-opus-4-6",
            permission=None,
        )
        assert "--dangerously-skip-permissions" in cmd
        assert "--permission-mode" not in cmd

    def test_gemini_capabilities_fallback_to_no_flags(self):
        """gemini + capabilities → fall back to no flags (extra_args kept)"""
        from ghdag.core.capabilities import TEXT_ONLY
        spec = ENGINE_SPECS["gemini"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", model="gemini-2.5-flash",
            permission=TEXT_ONLY,
        )
        assert "--permission-mode" not in cmd
        assert "--approval-mode yolo" in cmd  # extra_args kept

    def test_shell_capabilities_no_change(self):
        """shell + capabilities → command unchanged"""
        from ghdag.core.capabilities import TEXT_ONLY
        spec = ENGINE_SPECS["shell"]
        cmd_no_caps = render_exec_command(spec, order_path="queue/order.sh", model=None)
        cmd_with_caps = render_exec_command(spec, order_path="queue/order.sh", model=None, permission=TEXT_ONLY)
        assert cmd_no_caps == cmd_with_caps

    def test_claude_stream_capabilities(self):
        """stream=True → render_exec_command includes stream-json flags"""
        caps = Permission(stream=True)
        spec = ENGINE_SPECS["claude"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", model="claude-opus-4-6",
            permission=caps,
        )
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--verbose" in cmd

    def test_cursor_stream_prefers_stream_json_without_duplicate(self):
        """With cursor stream, stream-json wins and --output-format appears once."""
        caps = Permission(stream=True)
        spec = ENGINE_SPECS["cursor"]
        cmd = render_exec_command(
            spec, order_path="queue/order.md", model="auto",
            permission=caps,
        )
        assert cmd.split().count("--output-format") == 1
        assert "--output-format stream-json" in cmd
        assert "--output-format json" not in cmd
