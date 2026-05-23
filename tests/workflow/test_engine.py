"""Tests for ghdag.workflow.engine — EngineAdapter, ClaudeAdapter, GeminiAdapter, get_adapter."""

from __future__ import annotations

import pytest

from ghdag.workflow.engine import (
    ClaudeAdapter,
    GeminiAdapter,
    CursorAdapter,
    ShellAdapter,
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
        assert "--model 'flash'" in line  # -m から --model に統一（#985）
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


class TestCursorAdapter:
    def setup_method(self):
        self.adapter = CursorAdapter()
        self.base_kwargs = dict(
            order_path="queue/ts-cursor-order-abc123.md",
            result_path="queue/ts-cursor-result-abc123.md",
            prompt="受け取った内容を実行して",
        )

    def test_name(self):
        assert self.adapter.name == "cursor"

    def test_with_model_no_depends(self):
        line = self.adapter.build_exec_line(
            uuid="abc123",
            model="gemini-3-flash",
            depends=[],
            **self.base_kwargs,
        )
        expected = (
            "abc123: agent --model 'gemini-3-flash' -p --force"
            " < queue/ts-cursor-order-abc123.md"
            " | tee -a queue/ts-cursor-result-abc123.md"
        )
        assert line == expected

    def test_no_model_flag_when_model_is_none(self):
        line = self.adapter.build_exec_line(
            uuid="def456",
            model=None,
            depends=[],
            **self.base_kwargs,
        )
        assert "--model" not in line
        assert "--force" in line

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
            model="gemini-3-flash",
            depends=["abc123", "def456"],
            **self.base_kwargs,
        )
        assert line.startswith("ghi789[depends:abc123,def456]:")

    def test_no_depends_no_bracket(self):
        line = self.adapter.build_exec_line(
            uuid="abc123",
            model="gemini-3-flash",
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
        assert "| tee -a queue/ts-cursor-result-abc123.md" in line

    def test_force_flag_always_present(self):
        line = self.adapter.build_exec_line(
            uuid="x",
            model=None,
            depends=[],
            **self.base_kwargs,
        )
        assert "--force" in line

    def test_uses_agent_cli_not_cursor(self):
        line = self.adapter.build_exec_line(
            uuid="x",
            model="gemini-3-flash",
            depends=[],
            **self.base_kwargs,
        )
        # cursor CLI のエントリポイントは `agent` バイナリ
        assert "agent " in line
        assert " | cursor " not in line

    def test_uses_stdin_redirect_not_pipe_with_prompt(self):
        line = self.adapter.build_exec_line(
            uuid="x",
            model=None,
            depends=[],
            **self.base_kwargs,
        )
        # -p に文字列を渡すと stdin が無視されるため、リダイレクト形式を使う
        assert "< queue/ts-cursor-order-abc123.md" in line
        assert "agent -p --force" in line
        assert "cat " not in line


class TestShellAdapter:
    def setup_method(self):
        self.adapter = ShellAdapter()
        self.base_kwargs = dict(
            order_path="queue/ts-shell-order-abc123.md",
            result_path="queue/ts-shell-result-abc123.md",
            prompt="受け取った内容を実行して",
        )

    def test_name(self):
        assert self.adapter.name == "shell"

    def test_build_exec_line_no_depends(self):
        line = self.adapter.build_exec_line(
            uuid="abc123",
            model="bash",
            depends=[],
            **self.base_kwargs,
        )
        expected = (
            "abc123: bash -o pipefail queue/ts-shell-order-abc123.md"
            " | tee -a queue/ts-shell-result-abc123.md"
        )
        assert line == expected

    def test_build_exec_line_with_depends(self):
        line = self.adapter.build_exec_line(
            uuid="def456",
            model=None,
            depends=["abc123", "xyz"],
            **self.base_kwargs,
        )
        assert line.startswith("def456[depends:abc123,xyz]: bash -o pipefail ")

    def test_model_is_ignored(self):
        """model パラメーターは無視され、コマンドに出現しない。"""
        line = self.adapter.build_exec_line(
            uuid="abc",
            model="claude-opus-4-7",
            depends=[],
            **self.base_kwargs,
        )
        assert "claude" not in line
        assert "--model" not in line

    def test_prompt_is_ignored(self):
        """prompt パラメーターは無視され、コマンドに出現しない。"""
        line = self.adapter.build_exec_line(
            uuid="abc",
            model=None,
            depends=[],
            order_path="queue/order.md",
            result_path="queue/result.md",
            prompt="このプロンプトは無視される",
        )
        assert "このプロンプトは無視される" not in line
        assert "-p" not in line

    def test_build_exec_record(self):
        result = self.adapter.build_exec_record(
            uuid="abc-123",
            model="bash",
            depends=["dep-456"],
            **self.base_kwargs,
        )
        assert result == {
            "uuid": "abc-123",
            "command": "bash -o pipefail queue/ts-shell-order-abc123.md",
            "depends": ["dep-456"],
            "result_path": "queue/ts-shell-result-abc123.md",
            "retry": 0,
            "annotations": {},
        }

    def test_record_no_tee_in_command(self):
        result = self.adapter.build_exec_record(
            uuid="x",
            model=None,
            depends=[],
            **self.base_kwargs,
        )
        assert "tee" not in result["command"]

    def test_pipefail_option_always_present(self):
        line = self.adapter.build_exec_line(
            uuid="x",
            model=None,
            depends=[],
            **self.base_kwargs,
        )
        assert "-o pipefail" in line


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

    def test_get_cursor_returns_cursor_adapter(self):
        adapter = get_adapter("cursor")
        assert isinstance(adapter, CursorAdapter)

    def test_get_shell_returns_shell_adapter(self):
        adapter = get_adapter("shell")
        assert isinstance(adapter, ShellAdapter)

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


# ---------------------------------------------------------------------------
# build_exec_record — AC1, AC2, AC10
# ---------------------------------------------------------------------------

class TestBuildExecRecord:
    BASE_KWARGS = dict(
        uuid="abc-123",
        order_path="queue/order.md",
        result_path="queue/result.md",
        prompt="受け取った内容を実行して",
        depends=["dep-456"],
    )

    def test_claude_full_record_ac1(self):
        result = ClaudeAdapter().build_exec_record(
            **self.BASE_KWARGS, model="claude-sonnet-4-6"
        )
        assert result == {
            "uuid": "abc-123",
            "command": (
                "cat queue/order.md"
                " | claude -p '受け取った内容を実行して'"
                " --model 'claude-sonnet-4-6'"
                " --dangerously-skip-permissions"
            ),
            "depends": ["dep-456"],
            "result_path": "queue/result.md",
            "retry": 0,
            "annotations": {},
        }

    def test_no_tee_in_command_all_adapters_ac2(self):
        kwargs = {**self.BASE_KWARGS, "model": "some-model"}
        for adapter in [ClaudeAdapter(), GeminiAdapter(), CursorAdapter(), ShellAdapter()]:
            result = adapter.build_exec_record(**kwargs)
            assert "tee" not in result["command"], f"{adapter.name}: command contains tee"

    def test_claude_no_model_flag_when_none_ac10(self):
        result = ClaudeAdapter().build_exec_record(**self.BASE_KWARGS, model=None)
        assert "--model" not in result["command"]
        assert "--dangerously-skip-permissions" in result["command"]

    def test_gemini_record(self):
        result = GeminiAdapter().build_exec_record(
            **self.BASE_KWARGS, model="flash"
        )
        assert result["uuid"] == "abc-123"
        assert "gemini -p" in result["command"]
        assert "--model 'flash'" in result["command"]  # -m から --model に統一（#985）
        assert "--approval-mode yolo" in result["command"]
        assert "tee" not in result["command"]
        assert result["result_path"] == "queue/result.md"
        assert result["retry"] == 0
        assert result["annotations"] == {}

    def test_gemini_no_model_flag_when_none(self):
        result = GeminiAdapter().build_exec_record(**self.BASE_KWARGS, model=None)
        assert "-m " not in result["command"]

    def test_cursor_record(self):
        result = CursorAdapter().build_exec_record(
            **self.BASE_KWARGS, model="gemini-3-flash"
        )
        assert "agent" in result["command"]
        assert "--model 'gemini-3-flash'" in result["command"]
        assert "-p --force" in result["command"]
        assert "tee" not in result["command"]
        assert result["result_path"] == "queue/result.md"

    def test_cursor_no_model_flag_when_none(self):
        result = CursorAdapter().build_exec_record(**self.BASE_KWARGS, model=None)
        assert "--model" not in result["command"]
        assert "--force" in result["command"]

    def test_result_path_is_separate_field_not_in_command(self):
        for adapter in [ClaudeAdapter(), GeminiAdapter(), CursorAdapter()]:
            result = adapter.build_exec_record(**self.BASE_KWARGS, model=None)
            assert result["result_path"] == "queue/result.md"
            assert "queue/result.md" not in result["command"]
