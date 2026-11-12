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
from ghdag.core.engine_spec import ENGINE_SPECS, EngineSpec, InputMode, PromptFlag

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

    def test_shell_model_is_none_in_record(self):
        """AC3: shell build_exec_record model field is None"""
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
        """AC3: _GenericAdapter(claude) build_exec_record output"""
        adapter = _GenericAdapter(ENGINE_SPECS["claude"])
        record = adapter.build_exec_record(
            uuid="abc-123",
            order_path="queue/order.md",
            result_path="queue/result.md",
            prompt="Execute the received content",
            model="claude-sonnet-4-6",
            depends=["dep-456"],
        )
        assert record == {
            "uuid": "abc-123",
            "engine": "claude",
            "model": "claude-sonnet-4-6",
            "command": (
                "claude -p --model 'claude-sonnet-4-6'"
                " --output-format stream-json --verbose"
                " --dangerously-skip-permissions"
                " < queue/order.md"
            ),
            "depends": ["dep-456"],
            "result_path": "queue/result.md",
            "retry": 0,
            "annotations": {},
        }

    def test_build_exec_record_all_four_engines(self):
        """AC3: build_exec_record works for all 4 engines"""
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


def test_workflow_import_keeps_builtin_adapter_resolution() -> None:
    """Issue sumipan/nexus#6451 — built-in engines resolve after importing ghdag.workflow."""
    import ghdag.workflow  # noqa: F401
    from ghdag.core.command import get_command_adapter

    adapter = get_command_adapter("claude")
    assert adapter.name == "claude"


# ---------------------------------------------------------------------------
# shell adapter via get_command_adapter
# ---------------------------------------------------------------------------

class TestShellAdapterViaGetAdapter:
    def setup_method(self):
        self.adapter = get_command_adapter("shell")
        self.base_kwargs = dict(
            order_path="queue/ts-shell-order-abc123.md",
            result_path="queue/ts-shell-result-abc123.md",
            prompt="Execute the received content",
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


def test_register_command_adapter_round_trip() -> None:
    """Issue sumipan/nexus#6535 — custom adapter registration round trip."""

    class TestAdapter:
        name = "_test_engine_"

        def build_exec_record(self, **kwargs):
            return {}

    original_custom = dict(_CUSTOM_ADAPTERS)
    try:
        register_command_adapter(TestAdapter())
        adapter = get_command_adapter("_test_engine_")
        assert isinstance(adapter, TestAdapter)
    finally:
        _CUSTOM_ADAPTERS.clear()
        _CUSTOM_ADAPTERS.update(original_custom)


# ---------------------------------------------------------------------------
# AC4: virtual engine registration tests
# ---------------------------------------------------------------------------

class TestVirtualEngineRegistration:
    def test_virtual_engine_via_engine_specs(self):
        """AC4: virtual engine temporarily registered in ENGINE_SPECS works via get_command_adapter"""
        test_spec = EngineSpec(
            name="_test_",
            cli="echo",
            input_mode=InputMode.STDIN,
            prompt_flag=PromptFlag.FLAG_ONLY,
            prompt_flag_token="-p",
            model_flag="--model",
            default_model=None,
            danger_flag=None,
            danger_flag_position="trailing",
        )
        ENGINE_SPECS["_test_"] = test_spec
        try:
            adapter = get_command_adapter("_test_")
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

    def test_claude_full_record_ac1(self):
        result = self._adapter("claude").build_exec_record(
            **self.BASE_KWARGS, model="claude-sonnet-4-6"
        )
        assert result == {
            "uuid": "abc-123",
            "engine": "claude",
            "model": "claude-sonnet-4-6",
            "command": (
                "claude -p --model 'claude-sonnet-4-6'"
                " --output-format stream-json --verbose"
                " --dangerously-skip-permissions"
                " < queue/order.md"
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
        assert "--model 'flash'" in result["command"]  # unified from -m to --model (#985)
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
        """AC1: existing keys (command, uuid, depends, result_path, retry, annotations) preserved for all engines"""
        for name in ("claude", "gemini", "cursor", "shell"):
            result = self._adapter(name).build_exec_record(**self.BASE_KWARGS, model=None)
            for key in ("uuid", "command", "depends", "result_path", "retry", "annotations"):
                assert key in result, f"{name}: missing key {key!r}"


# ---------------------------------------------------------------------------
# _GenericAdapter.build_exec_record + capabilities (AC4, AC9)
# ---------------------------------------------------------------------------

class TestGenericAdapterCapabilities:
    BASE_KWARGS = dict(
        uuid="abc-123",
        order_path="queue/order.md",
        result_path="queue/result.md",
        prompt="Execute the received content",
        depends=[],
        model="claude-sonnet-4-6",
    )

    def test_claude_text_only_capabilities_add_permission_mode_flag(self):
        """AC4: _GenericAdapter(claude) + permission=TEXT_ONLY → command includes --permission-mode default"""
        from ghdag.core.capabilities import TEXT_ONLY
        adapter = _GenericAdapter(ENGINE_SPECS["claude"])
        record = adapter.build_exec_record(**self.BASE_KWARGS, permission=TEXT_ONLY)
        assert "--permission-mode" in record["command"]
        assert "default" in record["command"]

    def test_claude_text_only_capabilities_omit_dangerously_skip(self):
        """AC4: TEXT_ONLY → no --dangerously-skip-permissions"""
        from ghdag.core.capabilities import TEXT_ONLY
        adapter = _GenericAdapter(ENGINE_SPECS["claude"])
        record = adapter.build_exec_record(**self.BASE_KWARGS, permission=TEXT_ONLY)
        assert "--dangerously-skip-permissions" not in record["command"]

    def test_claude_capabilities_none_keeps_default_dangerously_skip(self):
        """AC9: no capabilities arg → same dict as before"""
        adapter = _GenericAdapter(ENGINE_SPECS["claude"])
        record_default = adapter.build_exec_record(**self.BASE_KWARGS)
        record_none = adapter.build_exec_record(**self.BASE_KWARGS, permission=None)
        assert record_default == record_none
        assert "--dangerously-skip-permissions" in record_default["command"]

    def test_cursor_capabilities_text_only_no_force(self):
        """cursor + permission=TEXT_ONLY → no --force"""
        from ghdag.core.capabilities import TEXT_ONLY
        adapter = _GenericAdapter(ENGINE_SPECS["cursor"])
        record = adapter.build_exec_record(
            uuid="u1", order_path="queue/order.md", result_path="queue/result.md",
            prompt="", model="auto", depends=[], permission=TEXT_ONLY,
        )
        assert "--force" not in record["command"]


def test_exec_record_permission_argument_preserves_schema() -> None:
    """Issue sumipan/nexus#6501 — build_exec_record(permission=) shape is unchanged."""
    from ghdag.core.capabilities import TEXT_ONLY

    adapter = _GenericAdapter(ENGINE_SPECS["claude"])
    baseline = adapter.build_exec_record(
        uuid="abc-123",
        order_path="queue/order.md",
        result_path="queue/result.md",
        prompt="Execute the received content",
        depends=[],
        model="claude-sonnet-4-6",
    )
    with_permission = adapter.build_exec_record(
        uuid="abc-123",
        order_path="queue/order.md",
        result_path="queue/result.md",
        prompt="Execute the received content",
        depends=[],
        model="claude-sonnet-4-6",
        permission=TEXT_ONLY,
    )
    assert set(with_permission) == set(baseline)
    assert set(with_permission["annotations"]) == set(baseline["annotations"])
    assert with_permission["command"] != baseline["command"]
    assert "permission" not in with_permission
    assert "capabilities" not in with_permission
