"""Shared token usage parsing utilities."""

from __future__ import annotations

import re

from ghdag.core.models.metrics import TokenUsage


def parse_token_usage_json(stdout_json: dict) -> TokenUsage:
    """claude --output-format json のレスポンスから TokenUsage を生成する。"""
    usage = stdout_json.get("usage") or {}
    input_tokens = usage.get("input_tokens") or 0
    output_tokens = usage.get("output_tokens") or 0
    total = input_tokens + output_tokens
    return TokenUsage(
        token_count=total if total > 0 else None,
        cost_usd=stdout_json.get("total_cost_usd"),
        cache_read_tokens=stdout_json.get("cache_read_input_tokens"),
        cache_creation_tokens=stdout_json.get("cache_creation_input_tokens"),
    )


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
