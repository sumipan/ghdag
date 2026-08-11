"""Tests for codex engine model registration and validation — Issue #2442."""

from __future__ import annotations

import pytest

from ghdag.llm._constants import DEFAULT_ENGINE_MODELS
from ghdag.llm.engines import EngineModelError, validate_engine_model


class TestCodexDefaultEngineModels:
    def test_codex_in_default_engine_models(self):
        """DEFAULT_ENGINE_MODELS に codex キーが存在する。"""
        assert "codex" in DEFAULT_ENGINE_MODELS

    def test_codex_has_four_models(self):
        """codex の許可モデルは 4 つ。"""
        assert len(DEFAULT_ENGINE_MODELS["codex"]) == 4

    def test_codex_models_content(self):
        """codex の許可モデルに 4 つの実測確認済みモデルが含まれる。"""
        models = DEFAULT_ENGINE_MODELS["codex"]
        assert "gpt-5.6-terra" in models
        assert "gpt-5.6-luna" in models
        assert "gpt-5.5" in models
        assert "gpt-5.4-mini" in models


class TestValidateCodexModel:
    def test_validate_codex_valid_model(self):
        """validate_engine_model("codex", ...) が許可モデルで成功する。"""
        result = validate_engine_model("codex", "gpt-5.6-terra")
        assert result == "gpt-5.6-terra"

    def test_validate_codex_default_model(self):
        """model=None でデフォルトモデルが返る。"""
        result = validate_engine_model("codex", None)
        assert result == "gpt-5.6-terra"

    def test_validate_codex_invalid_model(self):
        """許可外モデルで EngineModelError が送出される。"""
        with pytest.raises(EngineModelError, match="gpt-5.6-pro"):
            validate_engine_model("codex", "gpt-5.6-pro")

    def test_validate_codex_all_allowed_models(self):
        """4 つの許可モデルがすべてバリデーションを通過する。"""
        for model in ["gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.4-mini"]:
            result = validate_engine_model("codex", model)
            assert result == model
