"""Tests for CodexJsonlAdapter (--json JSONL → result / session_id / error).

Fixtures are slimmed from real `codex exec --json` captures (2026-09-09):
success / empty agent_message / auth failure (401 Unauthorized error events).
"""
from __future__ import annotations

from pathlib import Path

from ghdag.core.ports.output import EngineErrorKind
from ghdag.llm.adapters import get_output_adapter
from ghdag.llm.adapters.codex_jsonl import CodexJsonlAdapter

_FIXTURES = Path(__file__).parent / 'fixtures'
_SUCCESS = (_FIXTURES / 'codex_jsonl_success.jsonl').read_bytes()
_EMPTY = (_FIXTURES / 'codex_jsonl_empty.jsonl').read_bytes()
_AUTH_FAIL = (_FIXTURES / 'codex_jsonl_auth_fail.jsonl').read_bytes()

class TestCodexJsonlAdapterExtraction:

    def test_extract_result_text_from_success(self):
        adapter = CodexJsonlAdapter()
        assert adapter.extract_result_text(_SUCCESS, b'') == b'pong'

    def test_extract_result_text_empty_message(self):
        adapter = CodexJsonlAdapter()
        assert adapter.extract_result_text(_EMPTY, b'') == b''

    def test_extract_error_from_auth_fail(self):
        adapter = CodexJsonlAdapter()
        err = adapter.extract_error(_AUTH_FAIL, b'')
        assert err is not None
        assert err.kind == EngineErrorKind.AUTH
        assert '401' in err.message or 'Unauthorized' in err.message or 'auth' in err.message.lower()

    def test_is_terminal_result_event(self):
        adapter = CodexJsonlAdapter()
        assert adapter.is_terminal_result_event({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'pong'}})
        assert not adapter.is_terminal_result_event({'type': 'turn.started'})
        assert not adapter.is_terminal_result_event({'type': 'item.completed', 'item': {'type': 'command_execution'}})

    def test_get_output_adapter_returns_codex_jsonl(self):
        adapter = get_output_adapter('codex')
        assert isinstance(adapter, CodexJsonlAdapter)
