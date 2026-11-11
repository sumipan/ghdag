"""Tests for ghdag.pipeline.llm_pipeline — LLMPipelineAPI (Issue #203)."""

from __future__ import annotations

import pytest
from ghdag_pipeline_conftest_helpers import _TEST_AUDIT_CTX, _make_api

from ghdag.core.models.workflow import StepConfig


class TestStepConfigPermission:
    def test_submit_text_only_permission_adds_mode_and_disallowed_tools(self):
        """AC5: permission='text_only' → exec record command has --permission-mode default --disallowed-tools"""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-sonnet-4-6", permission="text_only")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--permission-mode" in record["command"]
        assert "default" in record["command"]
        assert "--disallowed-tools" in record["command"]

    def test_submit_text_only_permission_omits_dangerously_skip(self):
        """AC5: permission='text_only' → no --dangerously-skip-permissions"""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-sonnet-4-6", permission="text_only")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--dangerously-skip-permissions" not in record["command"]

    def test_submit_default_permission_applies_text_only_preset(self):
        """AC10: permission=None (default) → TEXT_ONLY (--permission-mode default --disallowed-tools)"""
        import json as _json
        api, _, _ = _make_api()
        steps_default = [StepConfig(template="brushup", model="claude-opus-4-6")]
        steps_none = [StepConfig(template="brushup", model="claude-opus-4-6", permission=None)]

        lines_default = api.submit(steps_default, {}, audit_context=_TEST_AUDIT_CTX)
        lines_none = api.submit(steps_none, {}, audit_context=_TEST_AUDIT_CTX)

        rec_default = _json.loads(lines_default[0])
        rec_none = _json.loads(lines_none[0])
        for record in (rec_default, rec_none):
            assert "--permission-mode default" in record["command"]
            assert "--disallowed-tools" in record["command"]
            assert "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch" in record["command"]
            assert "--dangerously-skip-permissions" not in record["command"]
            assert record["annotations"].get("safe_default_applied") is True
            assert record["annotations"].get("safe_default_preset") == "text_only"

    def test_submit_unknown_permission_preset_raises(self):
        """AC12: permission='unknown_preset' → ConfigError(STEP_PERMISSION_UNKNOWN)"""
        from ghdag.core.exceptions import ConfigError

        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-sonnet-4-6", permission="unknown_preset")]
        with pytest.raises(ConfigError, match="unknown_preset") as exc_info:
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)
        assert exc_info.value.code == "STEP_PERMISSION_UNKNOWN"

    def test_permission_dangerous_full_access(self):
        """permission='dangerous_full_access' → --permission-mode bypassPermissions"""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-sonnet-4-6", permission="dangerous_full_access")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--permission-mode" in record["command"]
        assert "bypassPermissions" in record["command"]

    def test_codex_submit_engine_isolation_zero_omits_skills_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Issue sumipan/nexus#6085: GHDAG_ENGINE_ISOLATION=0 must not add skills off."""
        import json as _json

        monkeypatch.setenv("GHDAG_ENGINE_ISOLATION", "0")
        api, _, _ = _make_api()
        steps = [
            StepConfig(
                template="brushup",
                engine="codex",
                model="gpt-5.6-terra",
                permission="text_only",
            )
        ]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "skills.include_instructions=false" not in record["command"]

    def test_permission_dangerous_full_access_codex(self):
        """codex + permission='dangerous_full_access' → sandbox bypass flags are attached.

        nexus#2558 regression: without the flags, codex starts as workspace-write and
        cannot write outside cwd (diary repo), so skills fail silently.
        """
        import json as _json
        api, _, _ = _make_api()
        steps = [
            StepConfig(
                template="brushup",
                engine="codex",
                model="gpt-5.6-terra",
                permission="dangerous_full_access",
            )
        ]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--dangerously-bypass-approvals-and-sandbox" in record["command"]
        assert record["command"].split().count("--json") == 1

    def test_safe_default_permission_applied_when_env_set_and_permission_none(self, monkeypatch):
        """AC2: with env set and permission=None, the safe default is applied."""
        import json as _json

        monkeypatch.setenv("GHDAG_SAFE_DEFAULT_PERMISSION", "text_only")
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6", permission=None)]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--permission-mode default" in record["command"]
        assert "--disallowed-tools" in record["command"]
        assert "--dangerously-skip-permissions" not in record["command"]
        assert record["annotations"].get("safe_default_applied") is True
        assert record["annotations"].get("safe_default_preset") == "text_only"
        assert "default_permission_applied" not in record["annotations"]

    def test_safe_default_rollback_dangerous_full_access(self, monkeypatch):
        """GHDAG_SAFE_DEFAULT_PERMISSION=dangerous_full_access can roll back to the dangerous default."""
        import json as _json

        monkeypatch.setenv("GHDAG_SAFE_DEFAULT_PERMISSION", "dangerous_full_access")
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6", permission=None)]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "bypassPermissions" in record["command"]
        assert "--disallowed-tools" not in record["command"]
        assert record["annotations"].get("safe_default_applied") is True
        assert record["annotations"].get("safe_default_preset") == "dangerous_full_access"

    def test_explicit_permission_wins_over_safe_default_env(self, monkeypatch):
        """AC3: an explicit permission overrides the env default."""
        import json as _json

        monkeypatch.setenv("GHDAG_SAFE_DEFAULT_PERMISSION", "text_only")
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6", permission="dangerous_full_access")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--permission-mode bypassPermissions" in record["command"]
        assert "safe_default_applied" not in record["annotations"]
        assert "safe_default_preset" not in record["annotations"]

    def test_cursor_dangerous_full_access_includes_force(self):
        """AC4: cursor + dangerous_full_access attaches --force."""
        import json as _json

        api, _, _ = _make_api()
        steps = [StepConfig(template="skill", model="auto", engine="cursor", permission="dangerous_full_access")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--force" in record["command"]

    def test_cursor_text_only_does_not_include_force(self):
        """AC5: cursor + text_only does not attach --force."""
        import json as _json

        api, _, _ = _make_api()
        steps = [StepConfig(template="skill", model="auto", engine="cursor", permission="text_only")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "--force" not in record["command"]

    def test_invalid_safe_default_permission_raises_value_error(self, monkeypatch):
        """AC6: invalid GHDAG_SAFE_DEFAULT_PERMISSION raises ValueError."""
        monkeypatch.setenv("GHDAG_SAFE_DEFAULT_PERMISSION", "invalid_preset")
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6", permission=None)]
        with pytest.raises(ValueError, match="GHDAG_SAFE_DEFAULT_PERMISSION"):
            api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

    def test_shell_engine_command_unchanged_when_safe_default_env_set(self, monkeypatch):
        """AC7: shell engine is unaffected by the safe-default env."""
        import json as _json

        monkeypatch.setenv("GHDAG_SAFE_DEFAULT_PERMISSION", "text_only")
        api, _, _ = _make_api()
        steps = [StepConfig(template="cp1-gate", model="bash", engine="shell", permission=None)]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert record["command"] == "bash -o pipefail queue/ts-claude-order-uuid.md"


# ---------------------------------------------------------------------------
# S0-2 (#1295): default permission audit annotations (AC1–AC3, AC7)
# ---------------------------------------------------------------------------


class TestDefaultPermissionAuditAnnotations:
    def test_submit_claude_default_sets_safe_default_annotations(self):
        """AC1: permission=None → safe_default_applied + safe_default_preset=text_only."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="claude-opus-4-6")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert record["annotations"]["safe_default_applied"] is True
        assert record["annotations"]["safe_default_preset"] == "text_only"
        assert "default_permission_applied" not in record["annotations"]
        assert "injected_danger_flag" not in record["annotations"]

    def test_submit_explicit_text_only_skips_default_annotation_key(self):
        """AC2: permission='text_only' → no default_permission_applied key."""
        import json as _json
        api, _, _ = _make_api()
        steps = [
            StepConfig(
                template="brushup",
                model="claude-sonnet-4-6",
                permission="text_only",
            )
        ]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "default_permission_applied" not in record["annotations"]

    def test_submit_gemini_skips_default_permission_annotation(self):
        """AC3: gemini (danger_flag=None) → no default_permission_applied key."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="gemini-2.5-pro", engine="gemini")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "default_permission_applied" not in record["annotations"]

    def test_submit_shell_skips_default_permission_annotation(self):
        """AC3: shell (danger_flag=None) → no default_permission_applied key."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="brushup", model="echo", engine="shell")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert "default_permission_applied" not in record["annotations"]

    def test_submit_cursor_default_text_only_without_force(self):
        """AC7: cursor + permission=None → TEXT_ONLY default (no --force)."""
        import json as _json
        api, _, _ = _make_api()
        steps = [StepConfig(template="impl", model="cursor", engine="cursor")]
        exec_lines = api.submit(steps, {}, audit_context=_TEST_AUDIT_CTX)

        record = _json.loads(exec_lines[0])
        assert record["annotations"]["safe_default_applied"] is True
        assert record["annotations"]["safe_default_preset"] == "text_only"
        assert "--force" not in record["command"]
        assert "default_permission_applied" not in record["annotations"]
