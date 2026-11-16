"""Tests for ghdag.pipeline.llm_pipeline — TaskQueue (Issue #203)."""

from __future__ import annotations

import pytest
from ghdag_pipeline_conftest_helpers import _TEST_AUDIT_CTX, _make_api

from ghdag.core.models.workflow import StepConfig


class TestStepConfigPermission:

    def test_submit_text_only_permission_omits_dangerously_skip(self):
        """AC5: permission='text_only' → no --dangerously-skip-permissions"""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-sonnet-4-6", permission="text_only")]
        exec_lines = api.enqueue(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--dangerously-skip-permissions" not in record["command"]

    def test_submit_unknown_permission_preset_raises(self):
        """AC12: permission='unknown_preset' → ConfigError(STEP_PERMISSION_UNKNOWN)"""
        from ghdag.core.exceptions import ConfigError

        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-sonnet-4-6", permission="unknown_preset")]
        with pytest.raises(ConfigError, match="unknown_preset") as exc_info:
            api.enqueue(steps, {}, audit_context=_TEST_AUDIT_CTX)
        assert exc_info.value.code == "STEP_PERMISSION_UNKNOWN"

    def test_invalid_safe_default_permission_raises_value_error(self, monkeypatch):
        """AC6: invalid GHDAG_SAFE_DEFAULT_PERMISSION raises ValueError."""
        monkeypatch.setenv("GHDAG_SAFE_DEFAULT_PERMISSION", "invalid_preset")
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6", permission=None)]
        with pytest.raises(ValueError, match="GHDAG_SAFE_DEFAULT_PERMISSION"):
            api.enqueue(steps, {}, audit_context=_TEST_AUDIT_CTX)

    def test_shell_engine_command_unchanged_when_safe_default_env_set(self, monkeypatch):
        """AC7: shell engine is unaffected by the safe-default env."""
        import json as _json

        monkeypatch.setenv("GHDAG_SAFE_DEFAULT_PERMISSION", "text_only")
        api, _, _ = _make_api()
        steps = [StepConfig(template="cp1-gate", model="bash", engine="shell", permission=None)]
        exec_lines = api.enqueue(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert record["command"].startswith("bash -o pipefail ")
        assert record["command"].endswith(".md")


# ---------------------------------------------------------------------------
# S0-2 (#1295): default permission audit annotations (AC1–AC3, AC7)
# ---------------------------------------------------------------------------
