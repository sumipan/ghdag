"""Tests for ghdag.workflow.engine — EngineAdapter, ClaudeAdapter, GeminiAdapter, get_adapter."""

from __future__ import annotations

import pytest

from ghdag.workflow.engine import (
    AdapterNotFoundError,
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

    def test_build_exec_record(self):
        result = self.adapter.build_exec_record(
            uuid="abc-123",
            model="bash",
            depends=["dep-456"],
            **self.base_kwargs,
        )
        assert result == {
            "uuid": "abc-123",
            "engine": "shell",
            "model": None,
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
        with pytest.raises(AdapterNotFoundError, match="Unknown engine"):
            get_adapter("unknown")

    def test_error_message_contains_available_engines(self):
        with pytest.raises(AdapterNotFoundError) as exc_info:
            get_adapter("unknown")
        msg = str(exc_info.value)
        assert "claude" in msg or "gemini" in msg

    def test_register_custom_adapter(self):
        class TestAdapter:
            name = "_test_engine_"

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
            "engine": "claude",
            "model": "claude-sonnet-4-6",
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

    # AC1: engine/model フィールドが build_exec_record 戻り値に含まれることを検証
    def test_claude_engine_model_fields_ac1(self):
        result = ClaudeAdapter().build_exec_record(**self.BASE_KWARGS, model="claude-opus-4-6")
        assert result["engine"] == "claude"
        assert result["model"] == "claude-opus-4-6"

    def test_cursor_engine_model_none_ac1(self):
        result = CursorAdapter().build_exec_record(**self.BASE_KWARGS, model=None)
        assert result["engine"] == "cursor"
        assert result["model"] is None

    def test_gemini_engine_model_fields_ac1(self):
        result = GeminiAdapter().build_exec_record(**self.BASE_KWARGS, model="gemini-2.5-flash")
        assert result["engine"] == "gemini"
        assert result["model"] == "gemini-2.5-flash"

    def test_shell_engine_model_none_ac1(self):
        result = ShellAdapter().build_exec_record(**self.BASE_KWARGS, model=None)
        assert result["engine"] == "shell"
        assert result["model"] is None

    def test_all_adapters_existing_keys_preserved_ac1(self):
        """AC1: 全Adapterで既存キー（command, uuid, depends, result_path, retry, annotations）が維持される"""
        for adapter in [ClaudeAdapter(), GeminiAdapter(), CursorAdapter(), ShellAdapter()]:
            result = adapter.build_exec_record(**self.BASE_KWARGS, model=None)
            for key in ("uuid", "command", "depends", "result_path", "retry", "annotations"):
                assert key in result, f"{adapter.name}: missing key {key!r}"
