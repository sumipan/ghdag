from __future__ import annotations

import json

from ghdag.llm.adapters.claude_json import ClaudeJsonAdapter
from ghdag.llm.adapters.claude_text import ClaudeTextAdapter
from ghdag.llm.adapters.codex import CodexAdapter
from ghdag.llm.adapters.cursor import CursorAdapter


def test_claude_json_extracts_session_id():
    adapter = ClaudeJsonAdapter()
    stdout = json.dumps({"session_id": "sess_abc", "result": "ok"}).encode("utf-8")
    assert adapter.extract_session_id(stdout, b"") == "sess_abc"


def test_claude_json_extracts_none_when_missing_session_id():
    adapter = ClaudeJsonAdapter()
    stdout = json.dumps({"result": "ok"}).encode("utf-8")
    assert adapter.extract_session_id(stdout, b"") is None


def test_cursor_extracts_chat_id_from_jsonl():
    adapter = CursorAdapter()
    stdout = b'{"type":"meta","chat_id":"chat_123"}\n{"type":"result","result":"ok"}\n'
    assert adapter.extract_session_id(stdout, b"") == "chat_123"


def test_codex_extracts_session_id_from_jsonl():
    adapter = CodexAdapter()
    stdout = b'{"type":"session.created","session_id":"codex_sess_1"}\n{"type":"turn.completed"}\n'
    assert adapter.extract_session_id(stdout, b"") == "codex_sess_1"


def test_claude_text_returns_none():
    adapter = ClaudeTextAdapter()
    assert adapter.extract_session_id(b"stdout", b"stderr") is None
