"""ghdag.llm.capabilities — capability-constraint value objects and presets for LLM calls (re-export shim)."""

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
from ghdag.core.exceptions import GhdagError


class LLMParseError(GhdagError):
    """Raised when a response violates the output_format contract."""

    code = "LLM_OUTPUT_NOT_JSON"
    exit_code = 3

    def __init__(self, raw: str, reason: str):
        self.raw = raw
        self.reason = reason
        super().__init__(
            f"LLM output parse failed: {reason}",
            hint="Return exactly one JSON object; surrounding prose and a code fence are allowed",
        )


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
