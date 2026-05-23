"""ghdag.llm._config — ENGINE_MODELS loading from YAML config"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from ghdag.llm._constants import DEFAULT_ENGINE_MODELS


class ConfigLoadError(ValueError):
    """Raised when the engine config file has an invalid structure."""


def load_engine_models(
    config_path: str | Path | None = None,
) -> dict[str, list[str]]:
    """Return ENGINE_MODELS.

    Args:
        config_path: YAML file path. If None, resolved in this order:
            1. GHDAG_LLM_MODELS env var path if set
            2. llm-models.yml in cwd
            3. Falls back to DEFAULT_ENGINE_MODELS

    Returns:
        {"claude": ["opus-4-6", ...], "gemini": ["2.5-pro", ...]}

    Raises:
        FileNotFoundError: config_path specified but does not exist
        ConfigLoadError: YAML structure is invalid (missing engines key, wrong types, etc.)
        yaml.YAMLError: YAML parse failure
    """
    if config_path is not None:
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        return _load_and_validate(path)

    # env var
    env_path = os.environ.get("GHDAG_LLM_MODELS")
    if env_path:
        env_file = Path(env_path)
        if env_file.exists():
            return _load_and_validate(env_file)

    # cwd
    cwd_path = Path.cwd() / "llm-models.yml"
    if cwd_path.exists():
        return _load_and_validate(cwd_path)

    # fallback
    return DEFAULT_ENGINE_MODELS


def _load_and_validate(path: Path) -> dict[str, list[str]]:
    """Load YAML file, validate structure, and return it."""
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "engines" not in data:
        raise ConfigLoadError(
            f"Invalid config structure: {path}\n"
            f"Top-level 'engines' key is required."
        )

    engines = data["engines"]
    if not isinstance(engines, dict):
        raise ConfigLoadError(
            f"Invalid config structure: {path}\n"
            f"'engines' value must be a dict."
        )

    for engine_name, models in engines.items():
        if not isinstance(models, list):
            raise ConfigLoadError(
                f"Invalid config structure: {path}\n"
                f"engines.{engine_name} value must be list[str]. "
                f"Got: {type(models).__name__}"
            )
        for item in models:
            if not isinstance(item, str):
                raise ConfigLoadError(
                    f"Invalid config structure: {path}\n"
                    f"engines.{engine_name} list elements must be str. "
                    f"Got: {type(item).__name__}"
                )

    return engines
