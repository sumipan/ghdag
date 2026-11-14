"""Tests for ghdag.llm — one-shot LLM call interface"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from ghdag.core.capabilities import JSON_ONLY, TEXT_ONLY
from ghdag.core.exceptions import LLMParseError
from ghdag.llm import Permission, call, list_engines, validate_engine_model
from ghdag.llm.engines import ENGINE_DEFAULTS, EngineModelError, LLMResult, list_models


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

    def test_ok_false_when_adapter_error_set(self):
        from ghdag.core.ports.output import EngineError, EngineErrorKind

        r = LLMResult(
            body="",
            stdout="x",
            stderr="",
            returncode=0,
            error=EngineError(kind=EngineErrorKind.CAPACITY, message="full", retryable=True),
        )
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
    def test_json_fail_skips_validation(self, mock_run: MagicMock):
        """returncode != 0 → skip JSON extraction"""
        mock_run.return_value = MagicMock(stdout="err", stderr="", returncode=1)
        result = call("hello", permission=JSON_ONLY, route=[("claude", None)])
        assert not result.ok

    @patch("ghdag.llm.engines.subprocess.run")
    def test_text_ok_no_validation(self, mock_run: MagicMock):
        """TEXT_ONLY → no JSON validation"""
        mock_run.return_value = MagicMock(stdout="not json", stderr="", returncode=0)
        result = call("hello", permission=TEXT_ONLY, route=[("claude", None)])
        assert result.body == "not json"

    @patch("ghdag.llm.engines.subprocess.run")
    def test_stream_extracts_result_from_jsonl(self, mock_run: MagicMock):
        """stream=True → extract final result from JSONL into body."""
        jsonl = (
            '{"type":"system","subtype":"init"}\n'
            '{"type":"assistant","message":{"content":[{"type":"text","text":"partial"}]}}\n'
            '{"type":"result","subtype":"success","result":"final answer"}\n'
        )
        mock_run.return_value = MagicMock(stdout=jsonl, stderr="", returncode=0)
        result = call("hello", permission=Permission(stream=True), route=[("claude", None)])
        assert result.body == "final answer"

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

    @patch("ghdag.llm.engines.subprocess.run")
    def test_claude_envelope_json_in_result_body(self, mock_run: MagicMock):
        """Claude JSON envelope with embedded JSON in result → parsed body (nexus #6246)."""
        envelope = (
            '{"type":"result","result":"Here: {\\"ok\\": true}","session_id":"s1"}'
        )
        mock_run.return_value = MagicMock(stdout=envelope, stderr="", returncode=0)
        result = call("hello", permission=JSON_ONLY, route=[("claude", None)])
        assert json.loads(result.body) == {"ok": True}
        assert result.raw_stdout == envelope

    @patch("ghdag.llm.engines.subprocess.run")
    def test_claude_envelope_non_json_result_raises_with_body_raw(self, mock_run: MagicMock):
        """Non-JSON result text → LLMParseError.raw is adapter body, not envelope."""
        envelope = '{"type":"result","result":"I cannot do that.","session_id":"s1"}'
        mock_run.return_value = MagicMock(stdout=envelope, stderr="", returncode=0)
        with pytest.raises(LLMParseError) as exc_info:
            call("hello", permission=JSON_ONLY, route=[("claude", None)])
        assert exc_info.value.raw == "I cannot do that."

    @patch("ghdag.llm.engines.subprocess.run")
    def test_fenced_json_raw_stdout_preserved(self, mock_run: MagicMock):
        """Fenced JSON in stdout → extracted body and raw_stdout preserved."""
        raw = '```json\n{"x": 1}\n```'
        mock_run.return_value = MagicMock(stdout=raw, stderr="", returncode=0)
        result = call("hello", permission=JSON_ONLY, route=[("claude", None)])
        assert json.loads(result.body) == {"x": 1}
        assert result.raw_stdout == raw

    @patch("ghdag.llm.engines.subprocess.run")
    def test_failed_call_skips_json_validation(self, mock_run: MagicMock):
        """returncode != 0 with Claude envelope → no JSON parse."""
        mock_run.return_value = MagicMock(
            stdout='{"type":"result","result":"x"}', stderr="", returncode=1
        )
        result = call("hello", permission=JSON_ONLY, route=[("claude", None)])
        assert result.returncode == 1


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
