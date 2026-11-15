"""Tests for ghdag.llm — one-shot LLM call interface"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ghdag.core.capabilities import JSON_ONLY
from ghdag.core.exceptions import LLMParseError
from ghdag.llm import Permission, call, validate_engine_model
from ghdag.llm.engines import ENGINE_DEFAULTS, EngineModelError, LLMResult, list_models


class TestEngineModels:

    def test_list_models_unknown_engine(self):
        """Unknown engine → EngineModelError"""
        with pytest.raises(EngineModelError, match="Unknown engine"):
            list_models("openai")


class TestValidateEngineModel:
    def test_valid_claude_model(self):
        """claude + allowed model → returned as-is"""
        result = validate_engine_model("claude", "claude-opus-4-6")
        assert result == "claude-opus-4-6"

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

    def test_unknown_engine(self):
        """Unknown engine → EngineModelError(ENGINE_UNKNOWN)"""
        with pytest.raises(EngineModelError, match="Unknown engine") as exc_info:
            validate_engine_model("openai", "gpt-4o")
        assert exc_info.value.code == "ENGINE_UNKNOWN"
        assert "claude" in (exc_info.value.hint or "")


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Permission
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# LLMResult
# ---------------------------------------------------------------------------


class TestLLMResult:
    def test_ok_success(self):
        r = LLMResult(body="out", stdout="out", stderr="", returncode=0)
        assert r.ok is True

    def test_ok_failure(self):
        r = LLMResult(body="", stdout="", stderr="err", returncode=1)
        assert r.ok is False


class TestCallOutputNormalization:
    @patch("ghdag.llm.engines.subprocess.run")
    def test_json_ok_valid_json(self, mock_run: MagicMock):
        """JSON_ONLY + valid JSON → body holds parsed JSON text."""
        mock_run.return_value = MagicMock(stdout='{"k": "v"}', stderr="", returncode=0)
        result = call("hello", permission=JSON_ONLY, route=[("claude", None)])
        assert result.body == '{"k": "v"}'
        assert result.ok

    @patch("ghdag.llm.engines.subprocess.run")
    def test_json_ok_invalid_json(self, mock_run: MagicMock):
        """JSON_ONLY + non-JSON → LLMParseError"""
        mock_run.return_value = MagicMock(stdout="not json", stderr="", returncode=0)
        with pytest.raises(LLMParseError) as exc_info:
            call("hello", permission=JSON_ONLY, route=[("claude", None)])
        assert exc_info.value.raw == "not json"

    @patch("ghdag.llm.engines.subprocess.run")
    def test_stream_json_output_validated_after_extraction(self, mock_run: MagicMock):
        """stream=True + output_format=json → JSON validate after extract."""
        jsonl = (
            '{"type":"result","subtype":"success","result":"{\\"k\\": \\"v\\"}"}\n'
        )
        mock_run.return_value = MagicMock(stdout=jsonl, stderr="", returncode=0)
        result = call("hello",
            permission=Permission(stream=True, output_format="json"), route=[("claude", None)])
        assert result.body == '{"k": "v"}'

    @patch("ghdag.llm.engines.subprocess.run")
    def test_stream_no_result_line_raises(self, mock_run: MagicMock):
        """stream JSONL without a result line → LLMParseError"""
        mock_run.return_value = MagicMock(stdout='{"type":"system"}\n', stderr="", returncode=0)
        with pytest.raises(LLMParseError, match="no result line"):
            call("hello", permission=Permission(stream=True), route=[("claude", None)])


class TestSupportsCapability:
    """supports_capability(engine, capability) — nexus #3034 AC-1 / CLAUDE.md §11."""

    def test_unknown_engine_and_capability_are_conservative_true(self) -> None:
        from ghdag.llm.engines import supports_capability

        assert supports_capability("unknown-engine", "resume") is True
        assert supports_capability("claude", "unknown_capability") is True

    def test_cursor_unsupported_includes_isolation_with_reason(self) -> None:
        """_UNSUPPORTED_CAPABILITIES['cursor'] includes isolation (nexus #3044)."""
        from ghdag.llm.engines import _UNSUPPORTED_CAPABILITIES

        assert "isolation" in _UNSUPPORTED_CAPABILITIES["cursor"]
