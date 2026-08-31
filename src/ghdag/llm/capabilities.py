"""ghdag.llm.capabilities — LLM 呼び出しの能力制約値オブジェクトとプリセット (re-export shim)."""

from __future__ import annotations

from ghdag.core.capabilities import (
    DANGEROUS_FULL_ACCESS,
    JSON_ONLY,
    PRESETS,
    READONLY_OBSERVE,
    TEXT_ONLY,
    WEB_RESEARCH,
    LLMCapabilities,
)
from ghdag.exceptions import GhdagError


class LLMParseError(GhdagError):
    """Raised when a response violates the output_format contract."""
    def __init__(self, raw: str, reason: str):
        self.raw = raw
        self.reason = reason
        super().__init__(f"LLM output parse failed: {reason}")


__all__ = [
    "LLMParseError",
    "LLMCapabilities",
    "TEXT_ONLY",
    "JSON_ONLY",
    "WEB_RESEARCH",
    "DANGEROUS_FULL_ACCESS",
    "READONLY_OBSERVE",
    "PRESETS",
]
