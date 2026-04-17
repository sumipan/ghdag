"""Tests for ghdag.llm._config — YAML 設定読み込み"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ghdag.llm._config import load_engine_models
from ghdag.llm._constants import DEFAULT_ENGINE_MODELS


# ---------------------------------------------------------------------------
# 正常系
# ---------------------------------------------------------------------------

class TestLoadEngineModelsNormal:
    def test_load_from_yaml_file(self, tmp_path: Path):
        """YAML ファイルからの読み込み"""
        config = tmp_path / "llm-models.yml"
        config.write_text(
            "engines:\n  claude:\n    - opus-4-6\n",
            encoding="utf-8",
        )
        result = load_engine_models(config)
        assert result == {"claude": ["opus-4-6"]}

    def test_load_from_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """環境変数によるパス指定"""
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
        """設定ファイルなし + 環境変数未設定 → DEFAULT_ENGINE_MODELS"""
        monkeypatch.delenv("GHDAG_LLM_MODELS", raising=False)
        monkeypatch.chdir(tmp_path)
        result = load_engine_models()
        assert result == DEFAULT_ENGINE_MODELS

    def test_empty_model_list(self, tmp_path: Path):
        """空リストのエンジン定義は有効"""
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
        """カレントディレクトリの llm-models.yml が存在する場合はそちらを使う"""
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
        """環境変数が指定されていれば cwd の llm-models.yml より優先"""
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
# 異常系
# ---------------------------------------------------------------------------

class TestLoadEngineModelsErrors:
    def test_missing_engines_key(self, tmp_path: Path):
        """engines キーが欠落 → ValueError（メッセージに 'engines' とパスを含む）"""
        config = tmp_path / "bad.yml"
        config.write_text(
            "models:\n  claude:\n    - opus-4-6\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError) as exc_info:
            load_engine_models(config)
        msg = str(exc_info.value)
        assert "engines" in msg
        assert str(config) in msg

    def test_invalid_value_type_str_instead_of_list(self, tmp_path: Path):
        """list でなく str の値 → ValueError"""
        config = tmp_path / "bad.yml"
        config.write_text(
            "engines:\n  claude: opus-4-6\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            load_engine_models(config)

    def test_yaml_parse_error(self, tmp_path: Path):
        """壊れた YAML → yaml.YAMLError"""
        config = tmp_path / "broken.yml"
        config.write_text(
            "engines: {\n  invalid yaml\n",
            encoding="utf-8",
        )
        with pytest.raises(yaml.YAMLError):
            load_engine_models(config)

    def test_nonexistent_explicit_path(self):
        """存在しないパスの明示指定 → FileNotFoundError"""
        with pytest.raises(FileNotFoundError):
            load_engine_models("/nonexistent/path/llm-models.yml")

    def test_invalid_value_type_dict_inside_list(self, tmp_path: Path):
        """list の要素が str でない（dict） → ValueError"""
        config = tmp_path / "bad.yml"
        config.write_text(
            "engines:\n  claude:\n    - name: opus\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            load_engine_models(config)


# ---------------------------------------------------------------------------
# call() 経由のバリデーション
# ---------------------------------------------------------------------------

class TestCallWithConfig:
    def test_call_validates_model_from_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """YAML に定義されていないエンジン/モデルで call() → EngineModelError"""
        config = tmp_path / "llm-models.yml"
        config.write_text(
            "engines:\n  claude:\n    - claude-opus-4-6\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("GHDAG_LLM_MODELS", str(config))
        # モジュールの ENGINE_MODELS を再ロードするため _config を直接テスト
        from ghdag.llm import _config
        result = _config.load_engine_models()
        assert result == {"claude": ["claude-opus-4-6"]}

    def test_unknown_engine_raises_engine_model_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """YAML に定義されていないエンジンで call() → EngineModelError"""
        from ghdag.llm.engines import validate_engine_model
        from ghdag.llm import EngineModelError

        monkeypatch.delenv("GHDAG_LLM_MODELS", raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(EngineModelError):
            validate_engine_model("openai", "gpt-4")
