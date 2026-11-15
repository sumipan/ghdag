"""Tests for ghdag.llm.adapters — EngineOutputAdapter unit tests (Issue #2266)."""

from __future__ import annotations

import pytest

from ghdag.core.models.metrics import TokenUsage
from ghdag.llm.adapters import get_output_adapter
from ghdag.llm.adapters.claude_json import ClaudeJsonAdapter
from ghdag.llm.adapters.cursor_stream import CursorStreamAdapter

# ---------------------------------------------------------------------------
# get_output_adapter registry
# ---------------------------------------------------------------------------


class TestGetOutputAdapter:
    def test_claude_returns_claude_json_adapter(self):
        adapter = get_output_adapter("claude")
        assert isinstance(adapter, ClaudeJsonAdapter)

    def test_cursor_returns_cursor_stream_adapter(self):
        adapter = get_output_adapter("cursor")
        assert isinstance(adapter, CursorStreamAdapter)


# ---------------------------------------------------------------------------
# TokenUsage dataclass
# ---------------------------------------------------------------------------

class TestTokenUsage:
    def test_all_none_by_default(self):
        u = TokenUsage()
        assert u.token_count is None
        assert u.cost_usd is None
        assert u.cache_read_tokens is None
        assert u.cache_creation_tokens is None

    def test_frozen(self):
        import dataclasses
        u = TokenUsage(token_count=100)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            u.token_count = 200  # type: ignore[misc]
