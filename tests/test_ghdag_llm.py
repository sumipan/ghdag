"""Tests for ghdag.llm — ワンショット LLM 呼び出しインタフェース"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from ghdag.llm import (
    ENGINE_DEFAULTS,
    EngineModelError,
    LLMResult,
    build_llm_cmd,
    call,
    list_engines,
    list_models,
    validate_engine_model,
)


# ---------------------------------------------------------------------------
# ホワイトリスト・検証
# ---------------------------------------------------------------------------

class TestEngineModels:
    def test_list_engines(self):
        """利用可能なエンジン一覧が返る"""
        engines = list_engines()
        assert "claude" in engines
        assert "gemini" in engines
        assert "cursor" in engines
        assert engines == sorted(engines)

    def test_list_models_claude(self):
        """claude エンジンの許可モデル一覧"""
        models = list_models("claude")
        assert "claude-sonnet-4-6" in models
        assert "claude-opus-4-6" in models
        assert models == sorted(models)

    def test_list_models_gemini(self):
        """gemini エンジンの許可モデル一覧"""
        models = list_models("gemini")
        assert "gemini-2.5-flash" in models
        assert "gemini-2.5-pro" in models

    def test_list_models_cursor(self):
        """cursor エンジンの許可モデル一覧"""
        models = list_models("cursor")
        assert "auto" in models
        assert "composer-2" in models

    def test_list_models_unknown_engine(self):
        """未知エンジン → EngineModelError"""
        with pytest.raises(EngineModelError, match="未知のエンジン"):
            list_models("openai")


class TestValidateEngineModel:
    def test_valid_claude_model(self):
        """claude + 許可モデル → そのまま返る"""
        result = validate_engine_model("claude", "claude-opus-4-6")
        assert result == "claude-opus-4-6"

    def test_valid_gemini_model(self):
        """gemini + 許可モデル → そのまま返る"""
        result = validate_engine_model("gemini", "gemini-2.5-pro")
        assert result == "gemini-2.5-pro"

    def test_default_model_claude(self):
        """model=None → エンジンデフォルト"""
        result = validate_engine_model("claude", None)
        assert result == ENGINE_DEFAULTS["claude"]

    def test_default_model_gemini(self):
        """model=None → エンジンデフォルト"""
        result = validate_engine_model("gemini", None)
        assert result == ENGINE_DEFAULTS["gemini"]

    def test_valid_cursor_model(self):
        """cursor + 許可モデル → そのまま返る"""
        result = validate_engine_model("cursor", "composer-2")
        assert result == "composer-2"

    def test_default_model_cursor(self):
        """cursor + model=None → "auto" がデフォルト"""
        result = validate_engine_model("cursor", None)
        assert result == ENGINE_DEFAULTS["cursor"]
        assert result == "auto"

    def test_unknown_engine(self):
        """未知エンジン → EngineModelError"""
        with pytest.raises(EngineModelError, match="未知のエンジン"):
            validate_engine_model("openai", "gpt-4o")

    def test_invalid_model(self):
        """許可外モデル → EngineModelError"""
        with pytest.raises(EngineModelError, match="許可リストにないモデル"):
            validate_engine_model("claude", "gpt-4o")

    def test_invalid_model_contains_info(self):
        """エラーメッセージにエンジン名とモデル名が含まれる"""
        with pytest.raises(EngineModelError) as exc_info:
            validate_engine_model("claude", "unknown-model")
        msg = str(exc_info.value)
        assert "claude" in msg
        assert "unknown-model" in msg


# ---------------------------------------------------------------------------
# コマンド構築
# ---------------------------------------------------------------------------

class TestBuildLLMCmd:
    def test_basic_claude(self):
        """基本的な claude コマンド構築"""
        cmd = build_llm_cmd("claude", "claude-opus-4-6", "hello")
        assert cmd == ["claude", "--model", "claude-opus-4-6", "-p", "hello"]

    def test_basic_gemini(self):
        """基本的な gemini コマンド構築"""
        cmd = build_llm_cmd("gemini", "gemini-2.5-flash", "hello")
        assert cmd == ["gemini", "--model", "gemini-2.5-flash", "-p", "hello"]

    def test_basic_cursor(self):
        """基本的な cursor コマンド構築（CLI は cursor-agent）"""
        cmd = build_llm_cmd("cursor", "composer-2", "hello")
        assert cmd == ["cursor-agent", "--model", "composer-2", "-p", "hello"]

    def test_dangerously_skip_permissions_claude(self):
        """claude で --dangerously-skip-permissions 付与"""
        cmd = build_llm_cmd(
            "claude", "claude-opus-4-6", "hello",
            dangerously_skip_permissions=True,
        )
        assert "--dangerously-skip-permissions" in cmd

    def test_dangerously_skip_permissions_gemini_ignored(self):
        """gemini では --dangerously-skip-permissions は付与されない"""
        cmd = build_llm_cmd(
            "gemini", "gemini-2.5-flash", "hello",
            dangerously_skip_permissions=True,
        )
        assert "--dangerously-skip-permissions" not in cmd


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


# ---------------------------------------------------------------------------
# call() — subprocess をモック
# ---------------------------------------------------------------------------

class TestCall:
    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_default_model(self, mock_run: MagicMock):
        """model=None → デフォルトモデルで呼び出し"""
        mock_run.return_value = MagicMock(
            stdout="response", stderr="", returncode=0,
        )
        result = call("hello", engine="claude")
        assert result.ok
        assert result.stdout == "response"
        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        assert ENGINE_DEFAULTS["claude"] in cmd

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_explicit_model(self, mock_run: MagicMock):
        """明示的モデル指定"""
        mock_run.return_value = MagicMock(
            stdout="ok", stderr="", returncode=0,
        )
        result = call("hello", engine="claude", model="claude-opus-4-6")
        assert result.ok
        cmd = mock_run.call_args[0][0]
        assert "claude-opus-4-6" in cmd

    def test_call_invalid_model(self):
        """許可外モデル → EngineModelError（subprocess 呼び出し前にエラー）"""
        with pytest.raises(EngineModelError):
            call("hello", engine="claude", model="bad-model")

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_with_stdin(self, mock_run: MagicMock):
        """stdin_text が渡される"""
        mock_run.return_value = MagicMock(
            stdout="ok", stderr="", returncode=0,
        )
        call("hello", engine="claude", stdin_text="input data")
        _, kwargs = mock_run.call_args
        assert kwargs["input"] == "input data"

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_with_timeout(self, mock_run: MagicMock):
        """timeout が渡される"""
        mock_run.return_value = MagicMock(
            stdout="ok", stderr="", returncode=0,
        )
        call("hello", engine="claude", timeout=30)
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 30


# ---------------------------------------------------------------------------
# CLI テスト
# ---------------------------------------------------------------------------

class TestCLI:
    def test_llm_list_engines(self):
        """ghdag llm --list-engines"""
        from ghdag.cli import main
        from io import StringIO
        import contextlib

        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            main(["llm", "--list-engines"])
        output = buf.getvalue()
        assert "claude" in output
        assert "gemini" in output

    def test_llm_list_models(self):
        """ghdag llm --list-models --engine claude"""
        from ghdag.cli import main
        from io import StringIO
        import contextlib

        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            main(["llm", "--list-models", "--engine", "claude"])
        output = buf.getvalue()
        assert "claude-opus-4-6" in output
        assert "claude-sonnet-4-6" in output

    def test_llm_list_models_unknown_engine(self):
        """ghdag llm --list-models --engine unknown → exit 1"""
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc_info:
            main(["llm", "--list-models", "--engine", "unknown"])
        assert exc_info.value.code == 1
