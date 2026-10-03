"""ghdag.llm.adapters — adapters converting CLI output to text/metrics.

The EngineOutputAdapter Protocol is responsible for extracting the body text
and TokenUsage from an engine-specific stdout format.
"""

from __future__ import annotations

from ghdag.core.models.metrics import FailureClass, TokenUsage
from ghdag.core.ports.output import EngineError, EngineOutputAdapter


class _PassthroughAdapter:
    """Default for unknown engines: stdout passthrough, usage is None."""

    def extract_result_text(self, stdout: bytes, stderr: bytes) -> bytes:
        return stdout

    def extract_token_usage(self, stdout: bytes, stderr: bytes) -> TokenUsage | None:
        return None

    def extract_session_id(self, stdout: bytes, stderr: bytes) -> str | None:
        return None

    def extract_error(self, stdout: bytes, stderr: bytes) -> EngineError | None:
        return None

    def classify_failure(
        self,
        returncode: int,
        stdout: bytes,
        stderr: bytes,
    ) -> FailureClass | None:
        return None


_DEFAULT_ADAPTER = _PassthroughAdapter()


def get_output_adapter(engine: str | None) -> EngineOutputAdapter:
    """Return the appropriate EngineOutputAdapter for an engine name.

    The claude engine output_format defaults to json / stream-json.
    cursor / codex return stream-capable adapters (accepting both single JSON and JSONL).
    """
    if engine == "claude":
        from ghdag.llm.adapters.claude_json import ClaudeJsonAdapter
        return ClaudeJsonAdapter()
    if engine == "cursor":
        from ghdag.llm.adapters.cursor_stream import CursorStreamAdapter
        return CursorStreamAdapter()
    if engine == "codex":
        from ghdag.llm.adapters.codex_jsonl import CodexJsonlAdapter
        return CodexJsonlAdapter()
    return _DEFAULT_ADAPTER


__all__ = ["EngineOutputAdapter", "get_output_adapter"]
