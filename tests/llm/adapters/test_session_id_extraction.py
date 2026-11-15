from __future__ import annotations

import json

from ghdag.llm.adapters.claude_json import ClaudeJsonAdapter
from ghdag.llm.adapters.codex import CodexAdapter

_CODEX_REAL_JSONL = b'{"type":"thread.started","thread_id":"01a0842c-88a5-7993-afd5-13562483ca9b"}\n{"type":"turn.started"}\n{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"pong"}}\n{"type":"turn.completed","usage":{"input_tokens":17235,"cached_input_tokens":11008,"cache_write_input_tokens":0,"output_tokens":5,"reasoning_output_tokens":0}}\n'

def test_claude_json_extracts_session_id():
    adapter = ClaudeJsonAdapter()
    stdout = json.dumps({'session_id': 'sess_abc', 'result': 'ok'}).encode('utf-8')
    assert adapter.extract_session_id(stdout, b'') == 'sess_abc'

def test_claude_json_extracts_none_when_missing_session_id():
    adapter = ClaudeJsonAdapter()
    stdout = json.dumps({'result': 'ok'}).encode('utf-8')
    assert adapter.extract_session_id(stdout, b'') is None

def test_codex_extracts_session_id_from_jsonl():
    """Backward compat for legacy session_id."""
    adapter = CodexAdapter()
    stdout = b'{"type":"session.created","session_id":"codex_sess_1"}\n{"type":"turn.completed"}\n'
    assert adapter.extract_session_id(stdout, b'') == 'codex_sess_1'
