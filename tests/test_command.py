"""Tests for build_llm_cmd / render_exec_command isolation opt-in (nexus #3174)."""

from __future__ import annotations

from ghdag.core.command import build_llm_cmd, render_exec_command
from ghdag.core.engine_spec import ENGINE_SPECS


class TestIsolationOptIn:
    """AC-1 / AC-2: --disable-slash-commands は isolation=True のときだけ付く。"""

    def test_build_llm_cmd_default_omits_disable_slash_commands(self) -> None:
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", isolation=False)
        assert "--disable-slash-commands" not in cmd
        assert "--disable-slash-commands" not in ENGINE_SPECS["claude"].extra_args

    def test_render_exec_command_default_omits_disable_slash_commands(self) -> None:
        cmd = render_exec_command(
            ENGINE_SPECS["claude"],
            order_path="queue/order.md",
            model="claude-opus-4-6",
            isolation=False,
        )
        assert "--disable-slash-commands" not in cmd

    def test_build_llm_cmd_isolation_true_includes_disable_slash_commands(self) -> None:
        cmd = build_llm_cmd("claude", "claude-sonnet-4-6", "hello", isolation=True)
        assert "--disable-slash-commands" in cmd

    def test_render_exec_command_isolation_true_includes_disable_slash_commands(
        self,
    ) -> None:
        cmd = render_exec_command(
            ENGINE_SPECS["claude"],
            order_path="queue/order.md",
            model="claude-opus-4-6",
            isolation=True,
        )
        assert "--disable-slash-commands" in cmd

    def test_isolation_true_does_not_affect_cursor(self) -> None:
        cmd = build_llm_cmd("cursor", "auto", "hello", isolation=True)
        assert "--disable-slash-commands" not in cmd
