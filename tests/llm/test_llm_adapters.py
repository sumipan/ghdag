"""Tests for CodexAdapter — Issue #2442."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ghdag.core.models.metrics import TokenUsage
from ghdag.llm.adapters.claude_json import ClaudeJsonAdapter
from ghdag.llm.adapters.codex import CodexAdapter

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

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


class TestClaudeJsonAdapterExtractQuotaWindows:
    def test_extracts_five_hour_and_seven_day_from_fixture(self):
        """Refs sumipan/nexus#6717 — fixture stream-json rate_limit_event."""
        stdout = (_FIXTURES / "claude_stream_rate_limit_event.jsonl").read_bytes()
        windows = ClaudeJsonAdapter().extract_quota_windows(stdout, b"")
        assert len(windows) == 2
        by_minutes = {w.window_minutes: w for w in windows}
        five = by_minutes[300]
        assert five.used_percent == 34.0
        assert five.resets_at == datetime.fromtimestamp(1794667200, tz=timezone.utc)
        seven = by_minutes[10080]
        assert seven.used_percent == 95.0
        assert seven.resets_at == datetime.fromtimestamp(1794682800, tz=timezone.utc)

    def test_uses_last_rate_limit_event_line(self):
        """Refs sumipan/nexus#6717 — later rate_limit_event wins."""
        later_five = 1794667300
        lines = [
            json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {
                        "unifiedWindows": {
                            "five_hour": {
                                "utilization": 0.1,
                                "resetsAt": 1,
                            },
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {
                        "unifiedWindows": {
                            "five_hour": {
                                "utilization": 0.5,
                                "resetsAt": later_five,
                            },
                        },
                    },
                }
            ),
        ]
        windows = ClaudeJsonAdapter().extract_quota_windows(
            "\n".join(lines).encode(),
            b"",
        )
        assert len(windows) == 1
        assert windows[0].used_percent == 50.0
        assert windows[0].resets_at == datetime.fromtimestamp(later_five, tz=timezone.utc)

    def test_drops_invalid_windows_and_keeps_valid_one(self):
        """Refs sumipan/nexus#6717 — invalid utilization or resetsAt are skipped."""
        payload = {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "unifiedWindows": {
                    "five_hour": {"utilization": 1.5, "resetsAt": 1794667200},
                    "seven_day": {"utilization": 0.2, "resetsAt": "not-an-int"},
                },
            },
        }
        extra_valid = {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "unifiedWindows": {
                    "five_hour": {"utilization": 1.5, "resetsAt": 1794667200},
                    "seven_day": {"utilization": 0.25, "resetsAt": 1794682800},
                },
            },
        }
        windows = ClaudeJsonAdapter().extract_quota_windows(
            json.dumps(extra_valid).encode(),
            b"",
        )
        assert len(windows) == 1
        assert windows[0].window_minutes == 10080
        assert windows[0].used_percent == 25.0
        windows_invalid = ClaudeJsonAdapter().extract_quota_windows(
            json.dumps(payload).encode(),
            b"",
        )
        assert windows_invalid == ()

    def test_returns_empty_without_rate_limit_event(self):
        """Refs sumipan/nexus#6717 — no event line yields empty tuple."""
        adapter = ClaudeJsonAdapter()
        assert adapter.extract_quota_windows(b"not json\n", b"") == ()
        assert adapter.extract_quota_windows(
            json.dumps({"type": "result", "result": "x"}).encode(),
            b"",
        ) == ()


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
