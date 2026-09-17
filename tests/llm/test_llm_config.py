"""Tests for codex engine model registration and validation — Issue #2442."""

from __future__ import annotations

import pytest

from ghdag.llm._constants import DEFAULT_ENGINE_MODELS
from ghdag.llm.engines import EngineModelError, validate_engine_model


class TestCodexDefaultEngineModels:
    def test_codex_in_default_engine_models(self):
        """DEFAULT_ENGINE_MODELS contains a codex key."""
        assert "codex" in DEFAULT_ENGINE_MODELS

    def test_codex_has_four_models(self):
        """codex has exactly four allowed models."""
        assert len(DEFAULT_ENGINE_MODELS["codex"]) == 4

    def test_codex_models_content(self):
        """codex allowed models include the four empirically verified names."""
        models = DEFAULT_ENGINE_MODELS["codex"]
        assert "gpt-5.6-terra" in models
        assert "gpt-5.6-luna" in models
        assert "gpt-5.5" in models
        assert "gpt-5.4-mini" in models


class TestValidateCodexModel:
    def test_validate_codex_valid_model(self):
        """validate_engine_model('codex', ...) succeeds for an allowed model."""
        result = validate_engine_model("codex", "gpt-5.6-terra")
        assert result == "gpt-5.6-terra"

    def test_validate_codex_default_model(self):
        """model=None returns the default model."""
        result = validate_engine_model("codex", None)
        assert result == "gpt-5.6-terra"

    def test_validate_codex_invalid_model(self):
        """A disallowed model raises EngineModelError."""
        with pytest.raises(EngineModelError, match="gpt-5.6-pro"):
            validate_engine_model("codex", "gpt-5.6-pro")

    def test_validate_codex_all_allowed_models(self):
        """All four allowed models pass validation."""
        for model in ["gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.4-mini"]:
            result = validate_engine_model("codex", model)
            assert result == model
