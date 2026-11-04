"""Tests for ghdag.llm — one-shot LLM call interface"""

from __future__ import annotations

import pytest

from ghdag.llm import (
    DANGEROUS_FULL_ACCESS,
    ENGINE_DEFAULTS,
    JSON_ONLY,
    TEXT_ONLY,
    WEB_RESEARCH,
    EngineModelError,
    LLMCapabilities,
    LLMParseError,
    LLMResult,
    build_llm_cmd,
    list_engines,
    list_models,
    validate_engine_model,
)


class TestEngineModels:
    def test_list_engines(self):
        """Returns the list of available engines"""
        engines = list_engines()
        assert "claude" in engines
        assert "gemini" in engines
        assert "cursor" in engines
        assert engines == sorted(engines)

    def test_list_models_claude(self):
        """Allowed models for the claude engine"""
        models = list_models("claude")
        assert "claude-sonnet-4-6" in models
        assert "claude-opus-4-6" in models
        assert models == sorted(models)

    def test_list_models_gemini(self, monkeypatch):
        """Default allowed models for gemini (excluding GHDAG_LLM_MODELS env influence)"""
        import ghdag.llm.engines as engines_mod
        from ghdag.llm._constants import DEFAULT_ENGINE_MODELS
        monkeypatch.setattr(engines_mod, "_ENGINE_MODELS", DEFAULT_ENGINE_MODELS)
        models = list_models("gemini")
        assert "gemini-2.5-flash" in models
        assert "gemini-2.5-pro" in models

    def test_list_models_cursor(self):
        """Allowed models for the cursor engine"""
        models = list_models("cursor")
        assert "auto" in models
        assert "composer-2" in models

    def test_list_models_unknown_engine(self):
        """Unknown engine → EngineModelError"""
        with pytest.raises(EngineModelError, match="Unknown engine"):
            list_models("openai")


class TestValidateEngineModel:
    def test_valid_claude_model(self):
        """claude + allowed model → returned as-is"""
        result = validate_engine_model("claude", "claude-opus-4-6")
        assert result == "claude-opus-4-6"

    def test_valid_gemini_model(self, monkeypatch):
        """gemini + allowed model → returned as-is (excluding GHDAG_LLM_MODELS env)"""
        import ghdag.llm.engines as engines_mod
        from ghdag.llm._constants import DEFAULT_ENGINE_MODELS
        monkeypatch.setattr(engines_mod, "_ENGINE_MODELS", DEFAULT_ENGINE_MODELS)
        result = validate_engine_model("gemini", "gemini-2.5-pro")
        assert result == "gemini-2.5-pro"

    def test_default_model_claude(self):
        """model=None → engine default"""
        result = validate_engine_model("claude", None)
        assert result == ENGINE_DEFAULTS["claude"]

    def test_default_model_gemini(self):
        """model=None → engine default"""
        result = validate_engine_model("gemini", None)
        assert result == ENGINE_DEFAULTS["gemini"]

    def test_valid_cursor_model(self):
        """cursor + allowed model → returned as-is"""
        result = validate_engine_model("cursor", "composer-2")
        assert result == "composer-2"

    def test_default_model_cursor(self):
        """cursor + model=None → defaults to \"auto\""""
        result = validate_engine_model("cursor", None)
        assert result == ENGINE_DEFAULTS["cursor"]
        assert result == "auto"

    def test_unknown_engine(self):
        """Unknown engine → EngineModelError"""
        with pytest.raises(EngineModelError, match="Unknown engine"):
            validate_engine_model("openai", "gpt-4o")

    def test_invalid_model(self):
        """Disallowed model → EngineModelError"""
        with pytest.raises(EngineModelError, match="Model not in allowlist"):
            validate_engine_model("claude", "gpt-4o")

    def test_invalid_model_contains_info(self):
        """Error message includes engine name and model name"""
        with pytest.raises(EngineModelError) as exc_info:
            validate_engine_model("claude", "unknown-model")
        msg = str(exc_info.value)
        assert "claude" in msg
        assert "unknown-model" in msg


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------

class TestBuildLLMCmd:
    def test_basic_claude(self):
        """Basic claude command construction (includes capabilities flags)"""
        cmd = build_llm_cmd("claude", "claude-opus-4-6", "hello")
        # Check basic elements
        assert cmd[0] == "claude"
        assert "--model" in cmd
        assert "claude-opus-4-6" in cmd
        assert "-p" in cmd
        # nexus #3805: STDIN engines keep the prompt body out of argv
        assert "hello" not in cmd
        # TEXT_ONLY default: permission-mode and disallowed-tools are attached
        assert "--permission-mode" in cmd
        assert "--disallowed-tools" in cmd

    def test_basic_gemini(self):
        """Basic gemini command construction (no capabilities flags)"""
        cmd = build_llm_cmd("gemini", "gemini-2.5-flash", "hello")
        assert cmd == ["gemini", "--model", "gemini-2.5-flash", "-p"]

    def test_basic_cursor(self):
        """Basic cursor command construction (CLI is cursor-agent, no capabilities flags)"""
        cmd = build_llm_cmd("cursor", "composer-2", "hello")
        assert cmd == ["cursor-agent", "--model", "composer-2", "-p"]


# ---------------------------------------------------------------------------
# LLMCapabilities
# ---------------------------------------------------------------------------

class TestCapabilities:
    def test_text_only_preset(self):
        assert TEXT_ONLY.permission_mode == "default"
        assert TEXT_ONLY.output_format == "text"
        assert "Bash" in TEXT_ONLY.disallowed_tools
        assert "Edit" in TEXT_ONLY.disallowed_tools
        assert "Write" in TEXT_ONLY.disallowed_tools
        assert "NotebookEdit" in TEXT_ONLY.disallowed_tools
        assert "WebFetch" in TEXT_ONLY.disallowed_tools
        assert "WebSearch" in TEXT_ONLY.disallowed_tools

    def test_json_only_preset(self):
        assert JSON_ONLY.permission_mode == "default"
        assert JSON_ONLY.output_format == "json"
        assert "Bash" in JSON_ONLY.disallowed_tools

    def test_web_research_preset(self):
        assert WEB_RESEARCH.permission_mode == "default"
        assert WEB_RESEARCH.output_format == "text"
        assert WEB_RESEARCH.web_search is True
        assert WEB_RESEARCH.allowed_tools == ()
        assert "Bash" in WEB_RESEARCH.disallowed_tools
        assert "Edit" in WEB_RESEARCH.disallowed_tools

    def test_default_capabilities_web_search_false(self):
        assert LLMCapabilities().web_search is False

    def test_dangerous_full_access_preset(self):
        assert DANGEROUS_FULL_ACCESS.permission_mode == "bypassPermissions"
        assert len(DANGEROUS_FULL_ACCESS.disallowed_tools) == 0
        assert len(DANGEROUS_FULL_ACCESS.allowed_tools) == 0

    def test_frozen(self):
        import dataclasses
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            TEXT_ONLY.permission_mode = "bypassPermissions"  # type: ignore

    def test_custom_capabilities(self):
        caps = LLMCapabilities(permission_mode="plan", output_format="json")
        assert caps.permission_mode == "plan"
        assert caps.output_format == "json"

    def test_force_cursor(self):
        """cursor with dangerously_skip_permissions=True attaches --force"""
        cmd = build_llm_cmd(
            "cursor", "auto", "hello",
            dangerously_skip_permissions=True,
        )
        assert "--force" in cmd

    def test_force_cursor_not_added_by_default(self):
        """cursor with dangerously_skip_permissions=False does not attach --force"""
        cmd = build_llm_cmd("cursor", "auto", "hello")
        assert "--force" not in cmd


# ---------------------------------------------------------------------------
# LLMResult
# ---------------------------------------------------------------------------

class TestLLMResult:
    def test_ok_success(self):
        r = LLMResult(stdout="out", stderr="", returncode=0)
        assert r.ok is True

    def test_ok_failure(self):
        r = LLMResult(stdout="", stderr="err", returncode=1)
        assert r.ok is False


class TestLLMResultValidate:
    def test_json_ok_valid_json(self):
        """JSON_ONLY + valid JSON → no LLMParseError"""
        r = LLMResult(stdout='{"k": "v"}', stderr="", returncode=0)
        result = r.validate(JSON_ONLY)
        assert result is r  # returns self

    def test_json_ok_invalid_json(self):
        """JSON_ONLY + non-JSON → LLMParseError"""
        r = LLMResult(stdout="not json", stderr="", returncode=0)
        with pytest.raises(LLMParseError) as exc_info:
            r.validate(JSON_ONLY)
        assert exc_info.value.raw == "not json"

    def test_json_fail_skips_validation(self):
        """returncode != 0 → skip validation"""
        r = LLMResult(stdout="err", stderr="", returncode=1)
        result = r.validate(JSON_ONLY)  # should not raise
        assert result is r

    def test_text_ok_no_validation(self):
        """TEXT_ONLY → no JSON validation"""
        r = LLMResult(stdout="not json", stderr="", returncode=0)
        result = r.validate(TEXT_ONLY)  # should not raise
        assert result is r

    def test_stream_extracts_result_from_jsonl(self):
        """stream=True → extract final result from JSONL"""
        jsonl = (
            '{"type":"system","subtype":"init"}\n'
            '{"type":"assistant","message":{"content":[{"type":"text","text":"partial"}]}}\n'
            '{"type":"result","subtype":"success","result":"final answer"}\n'
        )
        r = LLMResult(stdout=jsonl, stderr="", returncode=0)
        result = r.validate(LLMCapabilities(stream=True))
        assert result.stdout == "final answer"

    def test_stream_json_output_validated_after_extraction(self):
        """stream=True + output_format=json → JSON validate after extract"""
        jsonl = (
            '{"type":"result","subtype":"success","result":"{\\"k\\": \\"v\\"}"}\n'
        )
        r = LLMResult(stdout=jsonl, stderr="", returncode=0)
        result = r.validate(LLMCapabilities(stream=True, output_format="json"))
        assert result.stdout == '{"k": "v"}'

    def test_stream_no_result_line_raises(self):
        """stream JSONL without a result line → LLMParseError"""
        r = LLMResult(stdout='{"type":"system"}\n', stderr="", returncode=0)
        with pytest.raises(LLMParseError, match="no result line"):
            r.validate(LLMCapabilities(stream=True))
class TestSupportsCapability:
    """supports_capability(engine, capability) — nexus #3034 AC-1 / CLAUDE.md §11."""

    @pytest.mark.parametrize(
        "engine,capability,expected",
        [
            ("claude", "resume", True),
            ("claude", "stream", True),
            ("claude", "output_format", True),
            ("cursor", "resume", True),
            ("cursor", "stream", True),
            ("cursor", "output_format", True),
            ("codex", "resume", True),
            ("codex", "stream", True),
            ("codex", "output_format", False),
            # nexus #3044 — isolation (global config isolation)
            ("claude", "isolation", True),
            ("codex", "isolation", True),
            ("cursor", "isolation", False),
            ("claude", "web_search", True),
            ("cursor", "web_search", True),
            ("codex", "web_search", True),
            ("gemini", "web_search", False),
            ("shell", "web_search", False),
        ],
    )
    def test_three_engines_core_capabilities(
        self, engine: str, capability: str, expected: bool
    ) -> None:
        from ghdag.llm.engines import supports_capability

        assert supports_capability(engine, capability) is expected

    def test_unknown_engine_and_capability_are_conservative_true(self) -> None:
        from ghdag.llm.engines import supports_capability

        assert supports_capability("unknown-engine", "resume") is True
        assert supports_capability("claude", "unknown_capability") is True

    def test_ignored_capability_not_counted_as_unsupported(self) -> None:
        """Attrs in _IGNORED are excluded from effective_unsupported and yield True."""
        from ghdag.llm.engines import supports_capability

        assert supports_capability("codex", "allowed_tools") is True
        assert supports_capability("cursor", "disallowed_tools") is True

    def test_cursor_unsupported_includes_isolation_with_reason(self) -> None:
        """_UNSUPPORTED_CAPABILITIES['cursor'] includes isolation (nexus #3044)."""
        from ghdag.llm.engines import _UNSUPPORTED_CAPABILITIES

        assert "isolation" in _UNSUPPORTED_CAPABILITIES["cursor"]
