"""Tests for CodexAdapter — Issue #2442."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ghdag.llm.adapters.claude_json import ClaudeJsonAdapter
from ghdag.llm.adapters.codex import CodexAdapter

_FIXTURES = Path(__file__).resolve().parent.parent / 'fixtures'
SAMPLE_JSONL = '\n'.join([json.dumps({'type': 'thread.started', 'thread_id': '019fee6d-9bdd-75e3-bcf6-4f760ab4fa66'}), json.dumps({'type': 'turn.started'}), json.dumps({'type': 'item.completed', 'item': {'id': 'item_0', 'type': 'agent_message', 'text': 'PONG'}}), json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 13024, 'cached_input_tokens': 9984, 'cache_write_input_tokens': 0, 'output_tokens': 6, 'reasoning_output_tokens': 0}})])
TURN_FAILED_JSONL = '\n'.join([json.dumps({'type': 'thread.started', 'thread_id': 'abc'}), json.dumps({'type': 'turn.started'}), json.dumps({'type': 'turn.failed', 'error': {'message': 'API error'}})])
MULTI_MESSAGE_JSONL = '\n'.join([json.dumps({'type': 'item.completed', 'item': {'id': 'item_0', 'type': 'agent_message', 'text': 'Hello'}}), json.dumps({'type': 'item.completed', 'item': {'id': 'item_1', 'type': 'agent_message', 'text': 'World'}}), json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 0, 'cache_write_input_tokens': 0, 'output_tokens': 10}})])

class TestCodexAdapterExtractText:

    def test_extract_text_ignores_non_agent_message(self):
        """Non-agent_message item types are ignored."""
        jsonl = '\n'.join([json.dumps({'type': 'item.completed', 'item': {'id': 'item_0', 'type': 'reasoning', 'text': 'Thinking...'}}), json.dumps({'type': 'item.completed', 'item': {'id': 'item_1', 'type': 'agent_message', 'text': 'Answer'}})])
        adapter = CodexAdapter()
        result = adapter.extract_result_text(jsonl.encode(), b'')
        assert result == b'Answer'

class TestClaudeJsonAdapterExtractQuotaWindows:

    def test_uses_last_rate_limit_event_line(self):
        """Refs sumipan/nexus#6717 — later rate_limit_event wins."""
        later_five = 1794667300
        lines = [json.dumps({'type': 'rate_limit_event', 'rate_limit_info': {'unifiedWindows': {'five_hour': {'utilization': 0.1, 'resetsAt': 1}}}}), json.dumps({'type': 'rate_limit_event', 'rate_limit_info': {'unifiedWindows': {'five_hour': {'utilization': 0.5, 'resetsAt': later_five}}}})]
        windows = ClaudeJsonAdapter().extract_quota_windows('\n'.join(lines).encode(), b'')
        assert len(windows) == 1
        assert windows[0].used_percent == 50.0
        assert windows[0].resets_at == datetime.fromtimestamp(later_five, tz=timezone.utc)

    def test_drops_invalid_windows_and_keeps_valid_one(self):
        """Refs sumipan/nexus#6717 — invalid utilization or resetsAt are skipped."""
        payload = {'type': 'rate_limit_event', 'rate_limit_info': {'unifiedWindows': {'five_hour': {'utilization': 1.5, 'resetsAt': 1794667200}, 'seven_day': {'utilization': 0.2, 'resetsAt': 'not-an-int'}}}}
        extra_valid = {'type': 'rate_limit_event', 'rate_limit_info': {'unifiedWindows': {'five_hour': {'utilization': 1.5, 'resetsAt': 1794667200}, 'seven_day': {'utilization': 0.25, 'resetsAt': 1794682800}}}}
        windows = ClaudeJsonAdapter().extract_quota_windows(json.dumps(extra_valid).encode(), b'')
        assert len(windows) == 1
        assert windows[0].window_minutes == 10080
        assert windows[0].used_percent == 25.0
        windows_invalid = ClaudeJsonAdapter().extract_quota_windows(json.dumps(payload).encode(), b'')
        assert windows_invalid == ()
