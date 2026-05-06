"""Parsers for engine/model detection and token count extraction."""

from __future__ import annotations

import re
import shlex

_KNOWN_ENGINES = {"claude", "gemini", "cursor"}


def parse_engine_model(command: str) -> tuple[str | None, str | None]:
    """コマンド文字列から engine と model を抽出する。判定不能なら (None, None)。"""
    if not command:
        return None, None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None, None
    if not tokens or tokens[0] not in _KNOWN_ENGINES:
        return None, None

    engine = tokens[0]
    model = None
    for i in range(1, len(tokens) - 1):
        if tokens[i] == "--model":
            model = tokens[i + 1]
            break
    return engine, model


def parse_token_count(engine: str | None, stderr_text: str) -> int | None:
    """stderr からトークン数を抽出する。取得不能なら None。"""
    if engine != "claude":
        return None

    m = re.search(r"Total tokens:\s*(\d+)", stderr_text)
    if m:
        return int(m.group(1))

    input_m = re.search(r'input_tokens["\s:]+(\d+)', stderr_text)
    output_m = re.search(r'output_tokens["\s:]+(\d+)', stderr_text)
    if input_m and output_m:
        return int(input_m.group(1)) + int(output_m.group(1))
    if input_m:
        return int(input_m.group(1))
    if output_m:
        return int(output_m.group(1))

    return None
