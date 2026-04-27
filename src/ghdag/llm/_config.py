"""ghdag.llm._config — ENGINE_MODELS の YAML 設定読み込み"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from ghdag.llm._constants import DEFAULT_ENGINE_MODELS


def load_engine_models(
    config_path: str | Path | None = None,
) -> dict[str, list[str]]:
    """ENGINE_MODELS を返す。

    Args:
        config_path: YAML ファイルパス。None の場合は探索順序に従う。
            探索順序:
            1. 環境変数 GHDAG_LLM_MODELS が指定されていればそのパス
            2. カレントディレクトリの llm-models.yml
            3. いずれも見つからなければ DEFAULT_ENGINE_MODELS にフォールバック

    Returns:
        {"claude": ["opus-4-6", ...], "gemini": ["2.5-pro", ...]}

    Raises:
        FileNotFoundError: config_path が明示指定されたが存在しない場合
        ValueError: YAML の構造が不正な場合（engines キーが無い、値が list[str] でない等）
        yaml.YAMLError: YAML のパースに失敗した場合
    """
    if config_path is not None:
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"設定ファイルが見つかりません: {path}")
        return _load_and_validate(path)

    # 環境変数
    env_path = os.environ.get("GHDAG_LLM_MODELS")
    if env_path:
        env_file = Path(env_path)
        if env_file.exists():
            return _load_and_validate(env_file)

    # カレントディレクトリ
    cwd_path = Path.cwd() / "llm-models.yml"
    if cwd_path.exists():
        return _load_and_validate(cwd_path)

    # フォールバック
    return DEFAULT_ENGINE_MODELS


def _load_and_validate(path: Path) -> dict[str, list[str]]:
    """YAML ファイルを読み込み、構造を検証して返す。"""
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "engines" not in data:
        raise ValueError(
            f"設定ファイルの構造が不正です: {path}\n"
            f"トップレベルに 'engines' キーが必要です。"
        )

    engines = data["engines"]
    if not isinstance(engines, dict):
        raise ValueError(
            f"設定ファイルの構造が不正です: {path}\n"
            f"'engines' の値は dict である必要があります。"
        )

    for engine_name, models in engines.items():
        if not isinstance(models, list):
            raise ValueError(
                f"設定ファイルの構造が不正です: {path}\n"
                f"engines.{engine_name} の値は list[str] である必要があります。"
                f"実際の型: {type(models).__name__}"
            )
        for item in models:
            if not isinstance(item, str):
                raise ValueError(
                    f"設定ファイルの構造が不正です: {path}\n"
                    f"engines.{engine_name} のリスト要素は str である必要があります。"
                    f"実際の型: {type(item).__name__}"
                )

    return engines
