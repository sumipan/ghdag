"""Tests for ghdag.llm._config — YAML config loading"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ghdag.llm._config import ConfigLoadError, load_engine_models
from ghdag.llm._constants import DEFAULT_ENGINE_MODELS

# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestLoadEngineModelsNormal:
    def test_load_from_yaml_file(self, tmp_path: Path):
        """Load from a YAML file"""
        config = tmp_path / "llm-models.yml"
        config.write_text(
            "engines:\n  claude:\n    - opus-4-6\n",
            encoding="utf-8",
        )
        result = load_engine_models(config)
        assert result == {"claude": ["opus-4-6"]}

    def test_load_from_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Path specified via environment variable"""
        config = tmp_path / "custom.yml"
        config.write_text(
            "engines:\n  gemini:\n    - 2.5-pro\n    - 2.5-flash\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("GHDAG_LLM_MODELS", str(config))
        result = load_engine_models()
        assert result == {"gemini": ["2.5-pro", "2.5-flash"]}

    def test_fallback_when_no_file_no_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """No config file + env unset → DEFAULT_ENGINE_MODELS"""
        monkeypatch.delenv("GHDAG_LLM_MODELS", raising=False)
        monkeypatch.chdir(tmp_path)
        result = load_engine_models()
        assert result == DEFAULT_ENGINE_MODELS

    def test_empty_model_list(self, tmp_path: Path):
        """Empty-list engine definitions are valid"""
        config = tmp_path / "llm-models.yml"
        config.write_text(
            "engines:\n  claude: []\n",
            encoding="utf-8",
        )
        result = load_engine_models(config)
        assert result == {"claude": []}

    def test_cwd_file_takes_precedence_over_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Prefer cwd llm-models.yml when it exists"""
        monkeypatch.delenv("GHDAG_LLM_MODELS", raising=False)
        config = tmp_path / "llm-models.yml"
        config.write_text(
            "engines:\n  claude:\n    - custom-model\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        result = load_engine_models()
        assert result == {"claude": ["custom-model"]}

    def test_env_var_takes_precedence_over_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Env path takes precedence over cwd llm-models.yml"""
        cwd_config = tmp_path / "llm-models.yml"
        cwd_config.write_text(
            "engines:\n  claude:\n    - cwd-model\n",
            encoding="utf-8",
        )
        env_config = tmp_path / "env.yml"
        env_config.write_text(
            "engines:\n  gemini:\n    - env-model\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("GHDAG_LLM_MODELS", str(env_config))
        monkeypatch.chdir(tmp_path)
        result = load_engine_models()
        assert result == {"gemini": ["env-model"]}


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

class TestLoadEngineModelsErrors:
    def test_missing_engines_key(self, tmp_path: Path):
        """Missing engines key → ValueError (message includes 'engines' and path)"""
        config = tmp_path / "bad.yml"
        config.write_text(
            "models:\n  claude:\n    - opus-4-6\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigLoadError) as exc_info:
            load_engine_models(config)
        msg = str(exc_info.value)
        assert "engines" in msg
        assert str(config) in msg

    def test_invalid_value_type_str_instead_of_list(self, tmp_path: Path):
        """str value instead of list → ValueError"""
        config = tmp_path / "bad.yml"
        config.write_text(
            "engines:\n  claude: opus-4-6\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigLoadError):
            load_engine_models(config)

    def test_yaml_parse_error(self, tmp_path: Path):
        """Broken YAML → yaml.YAMLError"""
        config = tmp_path / "broken.yml"
        config.write_text(
            "engines: {\n  invalid yaml\n",
            encoding="utf-8",
        )
        with pytest.raises(yaml.YAMLError):
            load_engine_models(config)

    def test_nonexistent_explicit_path(self):
        """Explicit nonexistent path → FileNotFoundError"""
        with pytest.raises(FileNotFoundError):
            load_engine_models("/nonexistent/path/llm-models.yml")

    def test_invalid_value_type_dict_inside_list(self, tmp_path: Path):
        """list element is not str (dict) → ValueError"""
        config = tmp_path / "bad.yml"
        config.write_text(
            "engines:\n  claude:\n    - name: opus\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigLoadError):
            load_engine_models(config)


# ---------------------------------------------------------------------------
# Validation via call()
# ---------------------------------------------------------------------------

class TestCallWithConfig:
    def test_call_validates_model_from_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """call() with engine/model not in YAML → EngineModelError"""
        config = tmp_path / "llm-models.yml"
        config.write_text(
            "engines:\n  claude:\n    - claude-opus-4-6\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("GHDAG_LLM_MODELS", str(config))
        # Test _config directly so module ENGINE_MODELS is reloaded
        from ghdag.llm import _config
        result = _config.load_engine_models()
        assert result == {"claude": ["claude-opus-4-6"]}

    def test_unknown_engine_raises_engine_model_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """call() with engine not in YAML → EngineModelError"""
        from ghdag.llm import EngineModelError
        from ghdag.llm.engines import validate_engine_model

        monkeypatch.delenv("GHDAG_LLM_MODELS", raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(EngineModelError):
            validate_engine_model("openai", "gpt-4")
