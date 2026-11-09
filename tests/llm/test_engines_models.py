"""Tests for ghdag.llm — one-shot LLM call interface"""

from __future__ import annotations

import json

import pytest

from ghdag.llm import (
    ENGINE_DEFAULTS,
    JSON_ONLY,
    TEXT_ONLY,
    EngineModelError,
    LLMCapabilities,
    LLMParseError,
    LLMResult,
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
        """Unknown engine → EngineModelError(ENGINE_UNKNOWN)"""
        with pytest.raises(EngineModelError, match="Unknown engine") as exc_info:
            validate_engine_model("openai", "gpt-4o")
        assert exc_info.value.code == "ENGINE_UNKNOWN"
        assert "claude" in (exc_info.value.hint or "")

    def test_invalid_model(self):
        """Disallowed model → EngineModelError(MODEL_NOT_ALLOWED)"""
        with pytest.raises(EngineModelError, match="Model not in allowlist") as exc_info:
            validate_engine_model("claude", "gpt-4o")
        assert exc_info.value.code == "MODEL_NOT_ALLOWED"
        assert exc_info.value.exit_code == 3

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

# ---------------------------------------------------------------------------
# LLMCapabilities
# ---------------------------------------------------------------------------

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

    def test_claude_envelope_json_in_result_body(self):
        """Claude JSON envelope with embedded JSON in result → parsed stdout (nexus #6246)."""
        envelope = (
            '{"type":"result","result":"Here: {\\"ok\\": true}","session_id":"s1"}'
        )
        r = LLMResult(stdout=envelope, stderr="", returncode=0)
        result = r.validate(JSON_ONLY, engine="claude")
        assert json.loads(result.stdout) == {"ok": True}
        assert result.raw_stdout == envelope

    def test_claude_envelope_non_json_result_raises_with_body_raw(self):
        """Non-JSON result text → LLMParseError.raw is adapter body, not envelope."""
        envelope = '{"type":"result","result":"I cannot do that.","session_id":"s1"}'
        r = LLMResult(stdout=envelope, stderr="", returncode=0)
        with pytest.raises(LLMParseError) as exc_info:
            r.validate(JSON_ONLY, engine="claude")
        assert exc_info.value.raw == "I cannot do that."

    def test_gemini_fenced_raw_stdout_preserved(self):
        """Gemini passthrough stdout with fence → extracted JSON and raw_stdout."""
        raw = '```json\n{"x": 1}\n```'
        r = LLMResult(stdout=raw, stderr="", returncode=0)
        result = r.validate(JSON_ONLY, engine="gemini")
        assert json.loads(result.stdout) == {"x": 1}
        assert result.raw_stdout == raw

    def test_failed_call_skips_json_validation(self):
        """returncode != 0 with Claude envelope → validate returns self unchanged."""
        r = LLMResult(stdout='{"type":"result","result":"x"}', stderr="", returncode=1)
        result = r.validate(JSON_ONLY, engine="claude")
        assert result is r


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
