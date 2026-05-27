"""Tests for ghdag.workflow.engine — _GenericAdapter, get_adapter."""

from __future__ import annotations

import pytest

from ghdag.workflow.engine import (
    AdapterNotFoundError,
    _CUSTOM_ADAPTERS,
    _GenericAdapter,
    get_adapter,
    register_adapter,
)
from ghdag.llm.spec import ENGINE_SPECS


# ---------------------------------------------------------------------------
# _GenericAdapter — AC3 統合テスト
# ---------------------------------------------------------------------------

class TestGenericAdapter:
    def test_name_from_spec(self):
        adapter = _GenericAdapter(ENGINE_SPECS["claude"])
        assert adapter.name == "claude"

    def test_all_engines_produce_correct_name(self):
        for name, spec in ENGINE_SPECS.items():
            adapter = _GenericAdapter(spec)
            assert adapter.name == name

    def test_shell_model_is_none_in_record(self):
        """AC3: shell の build_exec_record の model フィールドが None"""
        adapter = _GenericAdapter(ENGINE_SPECS["shell"])
        record = adapter.build_exec_record(
            uuid="x",
            order_path="q/o.md",
            result_path="q/r.md",
            prompt="",
            model="bash",
            depends=[],
        )
        assert record["model"] is None

    def test_build_exec_record_matches_expected_claude(self):
        """AC3: _GenericAdapter(claude) の build_exec_record 出力"""
        adapter = _GenericAdapter(ENGINE_SPECS["claude"])
        record = adapter.build_exec_record(
            uuid="abc-123",
            order_path="queue/order.md",
            result_path="queue/result.md",
            prompt="受け取った内容を実行して",
            model="claude-sonnet-4-6",
            depends=["dep-456"],
        )
        assert record == {
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

    def test_build_exec_record_all_four_engines(self):
        """AC3: 4 エンジン全てで build_exec_record が正常動作"""
        kwargs = dict(
            uuid="u1",
            order_path="q/o.md",
            result_path="q/r.md",
            prompt="p",
            model=None,
            depends=[],
        )
        for name in ("claude", "gemini", "cursor", "shell"):
            adapter = _GenericAdapter(ENGINE_SPECS[name])
            record = adapter.build_exec_record(**kwargs)
            for key in ("uuid", "engine", "model", "command", "depends", "result_path", "retry", "annotations"):
                assert key in record, f"{name}: missing key {key!r}"


# ---------------------------------------------------------------------------
# Removed deprecated aliases
# ---------------------------------------------------------------------------

class TestRemovedDeprecatedAliases:
    @pytest.mark.parametrize(
        "import_stmt",
        [
            "from ghdag.workflow.engine import ClaudeAdapter",
            "from ghdag.workflow.engine import GeminiAdapter",
            "from ghdag.workflow.engine import CursorAdapter",
            "from ghdag.workflow.engine import ShellAdapter",
        ],
    )
    def test_deprecated_alias_import_raises(self, import_stmt: str):
        with pytest.raises(ImportError):
            exec(import_stmt, {})  # noqa: S102


# ---------------------------------------------------------------------------
# shell adapter via get_adapter
# ---------------------------------------------------------------------------

class TestShellAdapterViaGetAdapter:
    def setup_method(self):
        self.adapter = get_adapter("shell")
        self.base_kwargs = dict(
            order_path="queue/ts-shell-order-abc123.md",
            result_path="queue/ts-shell-result-abc123.md",
            prompt="受け取った内容を実行して",
        )

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
# get_adapter / register_adapter — AC3, AC4
# ---------------------------------------------------------------------------

class TestGetAdapter:
    def test_get_claude_returns_generic_adapter_with_correct_name(self):
        """AC3: get_adapter("claude") は _GenericAdapter を返す"""
        adapter = get_adapter("claude")
        assert isinstance(adapter, _GenericAdapter)
        assert adapter.name == "claude"

    def test_get_gemini_returns_generic_adapter_with_correct_name(self):
        adapter = get_adapter("gemini")
        assert isinstance(adapter, _GenericAdapter)
        assert adapter.name == "gemini"

    def test_get_cursor_returns_generic_adapter_with_correct_name(self):
        adapter = get_adapter("cursor")
        assert isinstance(adapter, _GenericAdapter)
        assert adapter.name == "cursor"

    def test_get_shell_returns_generic_adapter_with_correct_name(self):
        adapter = get_adapter("shell")
        assert isinstance(adapter, _GenericAdapter)
        assert adapter.name == "shell"

    def test_unknown_engine_raises_value_error(self):
        with pytest.raises(AdapterNotFoundError, match="Unknown engine"):
            get_adapter("unknown")

    def test_error_message_contains_available_engines(self):
        with pytest.raises(AdapterNotFoundError) as exc_info:
            get_adapter("unknown")
        msg = str(exc_info.value)
        assert "claude" in msg or "gemini" in msg

    def test_register_custom_adapter(self):
        """カスタムアダプターを register_adapter で登録し get_adapter で取得できる"""
        class TestAdapter:
            name = "_test_engine_"
            def build_exec_record(self, **kwargs):
                return {}

        original_custom = dict(_CUSTOM_ADAPTERS)
        try:
            register_adapter(TestAdapter())
            adapter = get_adapter("_test_engine_")
            assert isinstance(adapter, TestAdapter)
        finally:
            _CUSTOM_ADAPTERS.clear()
            _CUSTOM_ADAPTERS.update(original_custom)


# ---------------------------------------------------------------------------
# AC4: 仮想エンジン登録テスト
# ---------------------------------------------------------------------------

class TestVirtualEngineRegistration:
    def test_virtual_engine_via_engine_specs(self):
        """AC4: ENGINE_SPECS に一時登録した仮想エンジンが get_adapter で動作する"""
        from ghdag.llm.spec import EngineSpec

        test_spec = EngineSpec(
            name="_test_",
            cli="echo",
            input_mode="cat_pipe",
            prompt_flag="-p",
            model_flag="--model",
            default_model=None,
            danger_flag=None,
            danger_flag_position="none",
        )
        ENGINE_SPECS["_test_"] = test_spec
        try:
            adapter = get_adapter("_test_")
            assert isinstance(adapter, _GenericAdapter)
            assert adapter.name == "_test_"

            record = adapter.build_exec_record(
                uuid="u1",
                order_path="q/o.md",
                result_path="q/r.md",
                prompt="hello",
                model="m1",
                depends=[],
            )
            assert record["engine"] == "_test_"
            assert record["model"] == "m1"
        finally:
            del ENGINE_SPECS["_test_"]

    def test_custom_adapter_takes_precedence_for_unregistered_engine(self):
        """カスタム Adapter は _CUSTOM_ADAPTERS から取得できる"""
        class MyAdapter:
            name = "_my_custom_"
            def build_exec_record(self, **kwargs):
                return {"custom": True}

        original_custom = dict(_CUSTOM_ADAPTERS)
        try:
            register_adapter(MyAdapter())
            adapter = get_adapter("_my_custom_")
            assert isinstance(adapter, MyAdapter)
            assert adapter.build_exec_record() == {"custom": True}
        finally:
            _CUSTOM_ADAPTERS.clear()
            _CUSTOM_ADAPTERS.update(original_custom)


# ---------------------------------------------------------------------------
# AC1: pipeline → workflow 逆依存の解消
# ---------------------------------------------------------------------------

class TestPipelineLayerIndependence:
    def test_llm_pipeline_does_not_import_workflow_engine(self):
        """AC1: llm_pipeline.py のソースに workflow.engine への import がない"""
        import inspect
        import ghdag.pipeline.llm_pipeline as mod

        source = inspect.getsource(mod)
        assert "from ghdag.workflow.engine import" not in source
        assert "from ghdag.workflow import engine" not in source


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

    def _adapter(self, name: str) -> _GenericAdapter:
        return _GenericAdapter(ENGINE_SPECS[name])

    def test_claude_full_record_ac1(self):
        result = self._adapter("claude").build_exec_record(
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
        for name in ("claude", "gemini", "cursor", "shell"):
            result = self._adapter(name).build_exec_record(**kwargs)
            assert "tee" not in result["command"], f"{name}: command contains tee"

    def test_claude_no_model_flag_when_none_ac10(self):
        result = self._adapter("claude").build_exec_record(**self.BASE_KWARGS, model=None)
        assert "--model" not in result["command"]
        assert "--dangerously-skip-permissions" in result["command"]

    def test_gemini_record(self):
        result = self._adapter("gemini").build_exec_record(
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
        result = self._adapter("gemini").build_exec_record(**self.BASE_KWARGS, model=None)
        assert "-m " not in result["command"]

    def test_cursor_record(self):
        result = self._adapter("cursor").build_exec_record(
            **self.BASE_KWARGS, model="gemini-3-flash"
        )
        assert "agent" in result["command"]
        assert "--model 'gemini-3-flash'" in result["command"]
        assert "-p --force" in result["command"]
        assert "tee" not in result["command"]
        assert result["result_path"] == "queue/result.md"

    def test_cursor_no_model_flag_when_none(self):
        result = self._adapter("cursor").build_exec_record(**self.BASE_KWARGS, model=None)
        assert "--model" not in result["command"]
        assert "--force" in result["command"]

    def test_result_path_is_separate_field_not_in_command(self):
        for name in ("claude", "gemini", "cursor"):
            result = self._adapter(name).build_exec_record(**self.BASE_KWARGS, model=None)
            assert result["result_path"] == "queue/result.md"
            assert "queue/result.md" not in result["command"]

    def test_claude_engine_model_fields_ac1(self):
        result = self._adapter("claude").build_exec_record(**self.BASE_KWARGS, model="claude-opus-4-6")
        assert result["engine"] == "claude"
        assert result["model"] == "claude-opus-4-6"

    def test_cursor_engine_model_none_ac1(self):
        result = self._adapter("cursor").build_exec_record(**self.BASE_KWARGS, model=None)
        assert result["engine"] == "cursor"
        assert result["model"] is None

    def test_gemini_engine_model_fields_ac1(self):
        result = self._adapter("gemini").build_exec_record(**self.BASE_KWARGS, model="gemini-2.5-flash")
        assert result["engine"] == "gemini"
        assert result["model"] == "gemini-2.5-flash"

    def test_shell_engine_model_none_ac1(self):
        result = self._adapter("shell").build_exec_record(**self.BASE_KWARGS, model=None)
        assert result["engine"] == "shell"
        assert result["model"] is None

    def test_all_adapters_existing_keys_preserved_ac1(self):
        """AC1: 全エンジンで既存キー（command, uuid, depends, result_path, retry, annotations）が維持される"""
        for name in ("claude", "gemini", "cursor", "shell"):
            result = self._adapter(name).build_exec_record(**self.BASE_KWARGS, model=None)
            for key in ("uuid", "command", "depends", "result_path", "retry", "annotations"):
                assert key in result, f"{name}: missing key {key!r}"
