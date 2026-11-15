"""Tests for CursorStreamAdapter (stream-json → result / session_id / error).

Fixtures are slimmed from real
`agent -p --force --trust --output-format stream-json --stream-partial-output`
captures (2026-09-09): success / empty result / auth failure (stderr only).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ghdag.llm.adapters import get_output_adapter
from ghdag.llm.adapters.cursor_stream import CursorStreamAdapter


@pytest.fixture(autouse=True)
def _stall_guard_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep cursor calls on the plain ``subprocess.run`` path these tests mock."""
    monkeypatch.setenv('GHDAG_ENGINE_STALL_GUARD', '0')
_FIXTURES = Path(__file__).parent / 'fixtures'
_STREAM_SUCCESS = (_FIXTURES / 'cursor_stream_success.jsonl').read_bytes()
_STREAM_EMPTY = (_FIXTURES / 'cursor_stream_empty.jsonl').read_bytes()
_AUTH_STDOUT = (_FIXTURES / 'cursor_stream_auth_fail.stdout').read_bytes()
_AUTH_STDERR = (_FIXTURES / 'cursor_stream_auth_fail.stderr').read_bytes()
_STREAM_REPRO = (_FIXTURES / 'cursor_stream_repro.jsonl').read_bytes()
_LEGACY_JSON = (_FIXTURES / 'cursor_legacy_json.json').read_bytes()
_RETRIABLE_STDERR = (_FIXTURES / 'cursor_stream_retriable_error.stderr').read_bytes()
_RETRIABLE_RECONNECT_STDERR = (_FIXTURES / 'cursor_stream_retriable_error_reconnect.stderr').read_bytes()

class TestCursorStreamAdapterExtraction:

    def test_extract_result_text_from_stream_success(self):
        adapter = CursorStreamAdapter()
        assert adapter.extract_result_text(_STREAM_SUCCESS, b'') == b'pong'

    def test_extract_result_text_repeated_leading_delta(self):
        adapter = CursorStreamAdapter()
        assert adapter.extract_result_text(_STREAM_REPRO, b'') == b'llamas are great.'

    def test_extract_result_text_empty_result(self):
        adapter = CursorStreamAdapter()
        assert adapter.extract_result_text(_STREAM_EMPTY, b'') == b''

    def test_is_terminal_result_event(self):
        adapter = CursorStreamAdapter()
        assert adapter.is_terminal_result_event({'type': 'result', 'result': 'pong'})
        assert not adapter.is_terminal_result_event({'type': 'assistant'})
        assert not adapter.is_terminal_result_event({'type': 'thinking'})

    def test_auth_fail_classifies_failure(self):
        adapter = CursorStreamAdapter()
        classified = adapter.classify_failure(1, _AUTH_STDOUT, _AUTH_STDERR)
        assert classified is not None

    def test_get_output_adapter_returns_cursor_stream(self):
        adapter = get_output_adapter('cursor')
        assert isinstance(adapter, CursorStreamAdapter)

class TestCursorStreamExtractError:

    def test_extract_error_unauthenticated_returns_none(self):
        adapter = CursorStreamAdapter()
        assert adapter.extract_error(b'', b'RetriableError: [unauthenticated] Error\n') is None

    def test_extract_error_empty_stderr_returns_none(self):
        adapter = CursorStreamAdapter()
        assert adapter.extract_error(b'', b'') is None

    def test_extract_error_invalid_utf8_returns_none(self):
        adapter = CursorStreamAdapter()
        assert adapter.extract_error(b'', b'\xff\xfe') is None

    def test_empty_available_models_stderr_with_trailing_newline(self):
        from ghdag.core.ports.output import EngineErrorKind
        stderr = b'Cannot use this model: auto. Available models:\n'
        adapter = CursorStreamAdapter()
        err = adapter.extract_error(b'', stderr)
        assert err is not None
        assert err.kind == EngineErrorKind.CAPACITY
        assert err.retryable is True
        assert err.message == 'Cannot use this model: auto. Available models:'

    def test_nonempty_available_models_stderr_returns_none(self):
        adapter = CursorStreamAdapter()
        stderr = b'Cannot use this model: foo. Available models: auto, gpt-5.3-codex\n'
        assert adapter.extract_error(b'', stderr) is None

    def test_success_stdout_ignores_empty_available_models_stderr(self):
        adapter = CursorStreamAdapter()
        stderr = b'Cannot use this model: auto. Available models:\n'
        assert adapter.extract_error(_STREAM_SUCCESS, stderr) is None

    def test_extract_error_success_stdout_ignores_retriable_stderr(self):
        adapter = CursorStreamAdapter()
        err = adapter.extract_error(_STREAM_SUCCESS, _RETRIABLE_STDERR)
        assert err is None

    def test_extract_error_result_payload_rate_limit(self):
        from ghdag.core.ports.output import EngineErrorKind
        payload = b'{"type": "result", "subtype": "error", "is_error": true, "result": "Rate limit exceeded"}\n'
        adapter = CursorStreamAdapter()
        err = adapter.extract_error(payload, b'')
        assert err is not None
        assert err.kind == EngineErrorKind.RATE_LIMIT
        assert err.retryable is True

    def test_extract_error_result_payload_unknown(self):
        from ghdag.core.ports.output import EngineErrorKind
        payload = b'{"type": "result", "subtype": "error", "is_error": true, "result": "something broke"}\n'
        adapter = CursorStreamAdapter()
        err = adapter.extract_error(payload, b'')
        assert err is not None
        assert err.kind == EngineErrorKind.UNKNOWN
        assert err.retryable is False
