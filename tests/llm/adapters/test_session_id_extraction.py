from __future__ import annotations

import json

from ghdag.llm.adapters.claude_json import ClaudeJsonAdapter
from ghdag.llm.adapters.claude_text import ClaudeTextAdapter
from ghdag.llm.adapters.codex import CodexAdapter
from ghdag.llm.adapters.cursor import CursorAdapter

# 実測 stdout fixtures（Issue #2968）
_CURSOR_REAL_JSON = (
    b'{"type":"result","subtype":"success","is_error":false,'
    b'"duration_ms":5607,"duration_api_ms":5607,"result":"pong",'
    b'"session_id":"85105031-11df-48a2-a791-812a0128b4cf",'
    b'"request_id":"4425966d-...",'
    b'"usage":{"inputTokens":7144,"outputTokens":73,'
    b'"cacheReadTokens":8064,"cacheWriteTokens":0}}'
)

_CODEX_REAL_JSONL = (
    b'{"type":"thread.started","thread_id":"01a0842c-88a5-7993-afd5-13562483ca9b"}\n'
    b'{"type":"turn.started"}\n'
    b'{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"pong"}}\n'
    b'{"type":"turn.completed","usage":{"input_tokens":17235,"cached_input_tokens":11008,'
    b'"cache_write_input_tokens":0,"output_tokens":5,"reasoning_output_tokens":0}}\n'
)


def test_claude_json_extracts_session_id():
    adapter = ClaudeJsonAdapter()
    stdout = json.dumps({"session_id": "sess_abc", "result": "ok"}).encode("utf-8")
    assert adapter.extract_session_id(stdout, b"") == "sess_abc"


def test_claude_json_extracts_none_when_missing_session_id():
    adapter = ClaudeJsonAdapter()
    stdout = json.dumps({"result": "ok"}).encode("utf-8")
    assert adapter.extract_session_id(stdout, b"") is None


def test_cursor_extracts_session_id_from_real_json():
    """実測 cursor JSON の session_id を抽出する。"""
    adapter = CursorAdapter()
    assert adapter.extract_session_id(_CURSOR_REAL_JSON, b"") == (
        "85105031-11df-48a2-a791-812a0128b4cf"
    )


def test_cursor_extracts_chat_id_from_jsonl():
    """旧形式 chat_id の後方互換。"""
    adapter = CursorAdapter()
    stdout = b'{"type":"meta","chat_id":"chat_123"}\n{"type":"result","result":"ok"}\n'
    assert adapter.extract_session_id(stdout, b"") == "chat_123"


def test_cursor_prefers_session_id_over_chat_id():
    adapter = CursorAdapter()
    stdout = (
        b'{"chat_id":"chat_old","session_id":"sess_new","result":"ok"}\n'
    )
    assert adapter.extract_session_id(stdout, b"") == "sess_new"


def test_cursor_text_stdout_has_no_session_id():
    adapter = CursorAdapter()
    assert adapter.extract_session_id(b"pong", b"") is None


def test_codex_extracts_thread_id_from_real_jsonl():
    """実測 codex JSONL の thread.started.thread_id を抽出する。"""
    adapter = CodexAdapter()
    assert adapter.extract_session_id(_CODEX_REAL_JSONL, b"") == (
        "01a0842c-88a5-7993-afd5-13562483ca9b"
    )


def test_codex_extracts_session_id_from_jsonl():
    """旧形式 session_id の後方互換。"""
    adapter = CodexAdapter()
    stdout = b'{"type":"session.created","session_id":"codex_sess_1"}\n{"type":"turn.completed"}\n'
    assert adapter.extract_session_id(stdout, b"") == "codex_sess_1"


def test_codex_prefers_thread_id_over_session_id():
    adapter = CodexAdapter()
    stdout = (
        b'{"type":"thread.started","thread_id":"thread_prefer"}\n'
        b'{"type":"session.created","session_id":"sess_legacy"}\n'
    )
    assert adapter.extract_session_id(stdout, b"") == "thread_prefer"


def test_claude_text_returns_none():
    adapter = ClaudeTextAdapter()
    assert adapter.extract_session_id(b"stdout", b"stderr") is None
