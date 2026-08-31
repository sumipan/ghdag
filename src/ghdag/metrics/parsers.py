"""Parsers for engine/model detection and token count extraction."""

from __future__ import annotations

import shlex

import shlex

from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.core.parsers import parse_token_count, parse_token_usage_json

__all__ = ["parse_engine_model", "parse_token_usage_json", "parse_token_count"]


def parse_engine_model(command: str) -> tuple[str | None, str | None]:
    """コマンド文字列から engine と model を抽出する。判定不能なら (None, None)。

    cat パイプ形式（例: cat order.md | claude -p ...）やパイプ後のトークンも検出する。
    spec.cli の集合に一致するトークンを左から探すため、_KNOWN_ENGINES の直書きが不要。
    """
    if not command:
        return None, None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None, None

    cli_to_spec = {spec.cli: spec for spec in ENGINE_SPECS.values()}
    engine_idx = next(
        (i for i, tok in enumerate(tokens) if tok in cli_to_spec),
        None,
    )
    if engine_idx is None:
        return None, None

    spec = cli_to_spec[tokens[engine_idx]]
    model = None
    if spec.model_flag:
        for i in range(engine_idx + 1, len(tokens) - 1):
            if tokens[i] == spec.model_flag:
                model = tokens[i + 1]
                break
    return spec.name, model
