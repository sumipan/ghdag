"""Tests for ghdag.core.command — _GenericAdapter, get_command_adapter."""

from __future__ import annotations

import pytest

from ghdag.core.command import (
    _CUSTOM_ADAPTERS,
    AdapterNotFoundError,
    _GenericAdapter,
    get_command_adapter,
    register_command_adapter,
)
from ghdag.core.engine_spec import ENGINE_SPECS

# ---------------------------------------------------------------------------
# _GenericAdapter — AC3 integration tests
# ---------------------------------------------------------------------------

class TestGenericAdapter:
    def test_name_from_spec(self):
        adapter = _GenericAdapter(ENGINE_SPECS["claude"])
        assert adapter.name == "claude"

    def test_all_engines_produce_correct_name(self):
        for name, spec in ENGINE_SPECS.items():
            adapter = _GenericAdapter(spec)
            assert adapter.name == name


# ---------------------------------------------------------------------------
# shell adapter via get_command_adapter
# ---------------------------------------------------------------------------

class TestShellAdapterViaGetCommandAdapter:
    def setup_method(self):
        self.adapter = get_command_adapter("shell")
        self.base_kwargs = dict(
            order_path="queue/ts-shell-order-abc123.md",
            result_path="queue/ts-shell-result-abc123.md",
            prompt="Execute the received content",
        )


# ---------------------------------------------------------------------------
# get_command_adapter / register_command_adapter — sumipan/nexus#6535
# ---------------------------------------------------------------------------


def test_get_command_adapter_resolves_known_and_unknown_engines() -> None:
    """Issue sumipan/nexus#6535 — built-in engines resolve; unknown names raise AdapterNotFoundError."""
    for name in ("claude", "gemini", "cursor", "shell"):
        adapter = get_command_adapter(name)
        assert isinstance(adapter, _GenericAdapter)
        assert adapter.name == name

    with pytest.raises(AdapterNotFoundError, match="Unknown engine"):
        get_command_adapter("unknown")
    with pytest.raises(AdapterNotFoundError) as exc_info:
        get_command_adapter("unknown")
    msg = str(exc_info.value)
    assert "claude" in msg or "gemini" in msg


# ---------------------------------------------------------------------------
# AC4: virtual engine registration tests
# ---------------------------------------------------------------------------

class TestVirtualEngineRegistration:
    def test_custom_adapter_takes_precedence_for_unregistered_engine(self):
        """Custom Adapter can be retrieved from _CUSTOM_ADAPTERS"""
        class MyAdapter:
            name = "_my_custom_"
            def build_exec_record(self, **kwargs):
                return {"custom": True}

        original_custom = dict(_CUSTOM_ADAPTERS)
        try:
            register_command_adapter(MyAdapter())
            adapter = get_command_adapter("_my_custom_")
            assert isinstance(adapter, MyAdapter)
            assert adapter.build_exec_record() == {"custom": True}
        finally:
            _CUSTOM_ADAPTERS.clear()
            _CUSTOM_ADAPTERS.update(original_custom)


class TestBuildExecRecord:
    BASE_KWARGS = dict(
        uuid="abc-123",
        order_path="queue/order.md",
        result_path="queue/result.md",
        prompt="Execute the received content",
        depends=["dep-456"],
    )

    def _adapter(self, name: str) -> _GenericAdapter:
        return _GenericAdapter(ENGINE_SPECS[name])

    def test_no_tee_in_command_all_adapters_ac2(self):
        kwargs = {**self.BASE_KWARGS, "model": "some-model"}
        for name in ("claude", "gemini", "cursor", "shell"):
            result = self._adapter(name).build_exec_record(**kwargs)
            assert "tee" not in result["command"], f"{name}: command contains tee"

    def test_claude_no_model_flag_when_none_ac10(self):
        result = self._adapter("claude").build_exec_record(**self.BASE_KWARGS, model=None)
        assert "--model" not in result["command"]
        assert "--dangerously-skip-permissions" in result["command"]

    def test_gemini_no_model_flag_when_none(self):
        result = self._adapter("gemini").build_exec_record(**self.BASE_KWARGS, model=None)
        assert "-m " not in result["command"]

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
