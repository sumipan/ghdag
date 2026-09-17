"""Tests for CodexAdapter — Issue #2442."""

from __future__ import annotations

import json

from ghdag.llm.adapters.codex import CodexAdapter
from ghdag.metrics.models import TokenUsage

SAMPLE_JSONL = "\n".join([
    json.dumps({"type": "thread.started", "thread_id": "019fee6d-9bdd-75e3-bcf6-4f760ab4fa66"}),
    json.dumps({"type": "turn.started"}),
    json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "PONG"}}),
    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 13024, "cached_input_tokens": 9984, "cache_write_input_tokens": 0, "output_tokens": 6, "reasoning_output_tokens": 0}}),
])

TURN_FAILED_JSONL = "\n".join([
    json.dumps({"type": "thread.started", "thread_id": "abc"}),
    json.dumps({"type": "turn.started"}),
    json.dumps({"type": "turn.failed", "error": {"message": "API error"}}),
])

MULTI_MESSAGE_JSONL = "\n".join([
    json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "Hello"}}),
    json.dumps({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "World"}}),
    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 0, "cache_write_input_tokens": 0, "output_tokens": 10}}),
])


class TestCodexAdapterExtractText:
    def test_extract_text_normal(self):
        """Extract agent_message text from a normal JSONL stream."""
        adapter = CodexAdapter()
        result = adapter.extract_result_text(SAMPLE_JSONL.encode(), b"")
        assert result == b"PONG"

    def test_extract_text_multi_messages(self):
        """Multiple agent_message texts are concatenated."""
        adapter = CodexAdapter()
        result = adapter.extract_result_text(MULTI_MESSAGE_JSONL.encode(), b"")
        assert result == b"Hello\nWorld"

    def test_extract_text_turn_failed(self):
        """JSONL with only turn.failed yields empty extracted text."""
        adapter = CodexAdapter()
        result = adapter.extract_result_text(TURN_FAILED_JSONL.encode(), b"")
        assert result == b""

    def test_extract_text_empty_stdout(self):
        """Empty stdout yields empty bytes."""
        adapter = CodexAdapter()
        result = adapter.extract_result_text(b"", b"")
        assert result == b""

    def test_extract_text_ignores_non_agent_message(self):
        """Non-agent_message item types are ignored."""
        jsonl = "\n".join([
            json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "reasoning", "text": "Thinking..."}}),
            json.dumps({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "Answer"}}),
        ])
        adapter = CodexAdapter()
        result = adapter.extract_result_text(jsonl.encode(), b"")
        assert result == b"Answer"


class TestCodexAdapterExtractUsage:
    def test_extract_usage_normal(self):
        """Extract TokenUsage from turn.completed."""
        adapter = CodexAdapter()
        usage = adapter.extract_token_usage(SAMPLE_JSONL.encode(), b"")
        assert usage == TokenUsage(
            token_count=13024 + 6,
            cache_read_tokens=9984,
            cache_creation_tokens=0,
            cost_usd=None,
        )

    def test_extract_usage_turn_failed(self):
        """turn.failed yields None usage."""
        adapter = CodexAdapter()
        usage = adapter.extract_token_usage(TURN_FAILED_JSONL.encode(), b"")
        assert usage is None

    def test_extract_usage_empty(self):
        """Empty stdout yields None usage."""
        adapter = CodexAdapter()
        usage = adapter.extract_token_usage(b"", b"")
        assert usage is None

    def test_extract_usage_cost_usd_always_none(self):
        """codex never emits cost_usd, so it is always None."""
        adapter = CodexAdapter()
        usage = adapter.extract_token_usage(SAMPLE_JSONL.encode(), b"")
        assert usage is not None
        assert usage.cost_usd is None
