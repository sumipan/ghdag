from __future__ import annotations

from ghdag.core.ports.output import EngineErrorKind
from ghdag.llm.adapters import get_output_adapter
from ghdag.llm.adapters.claude_json import ClaudeJsonAdapter
from ghdag.llm.adapters.codex import CodexAdapter
from ghdag.llm.adapters.cursor import CursorAdapter


def test_codex_extract_error_capacity_event() -> None:
    adapter = CodexAdapter()
    stdout = b'{"type":"error","message":"Selected model is at capacity."}\n'
    err = adapter.extract_error(stdout, b"")
    assert err is not None
    assert err.kind is EngineErrorKind.CAPACITY
    assert err.retryable is True


def test_codex_extract_error_rate_limit_event() -> None:
    adapter = CodexAdapter()
    stdout = b'{"type":"turn.failed","error":"Rate limit exceeded"}\n'
    err = adapter.extract_error(stdout, b"")
    assert err is not None
    assert err.kind is EngineErrorKind.RATE_LIMIT
    assert err.retryable is True


def test_codex_extract_error_normal_stream_returns_none() -> None:
    adapter = CodexAdapter()
    stdout = b'\n'.join(
        [
            b'{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}',
            b'{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}',
        ]
    )
    assert adapter.extract_error(stdout, b"") is None


def test_codex_quota_exhausted_extracts_resume_at() -> None:
    adapter = CodexAdapter()
    stdout = (
        b'{"type":"error","message":"quota exhausted; '
        b'resets at 2026-09-02T17:00:00+09:00"}\n'
    )
    err = adapter.extract_error(stdout, b"")
    assert err is not None
    assert err.kind is EngineErrorKind.QUOTA_EXHAUSTED
    assert err.resume_at is not None


def test_claude_extract_error_is_error_true() -> None:
    adapter = ClaudeJsonAdapter()
    stdout = b'{"is_error":true,"error":{"message":"overloaded"}}'
    err = adapter.extract_error(stdout, b"")
    assert err is not None
    assert err.kind is EngineErrorKind.CAPACITY
    assert err.retryable is True


def test_claude_extract_error_normal_json_returns_none() -> None:
    adapter = ClaudeJsonAdapter()
    stdout = b'{"result":"hello","type":"result"}'
    assert adapter.extract_error(stdout, b"") is None


def test_claude_quota_exhausted_extracts_resume_at() -> None:
    adapter = ClaudeJsonAdapter()
    stdout = (
        b'{"is_error":true,"error":{"message":"quota exhausted. '
        b'Try again at 2026-09-02T17:00:00+09:00"}}'
    )
    err = adapter.extract_error(stdout, b"")
    assert err is not None
    assert err.kind is EngineErrorKind.QUOTA_EXHAUSTED
    assert err.retryable is False
    assert err.resume_at is not None


def test_claude_rate_limit_is_not_quota_exhausted() -> None:
    adapter = ClaudeJsonAdapter()
    stdout = b'{"is_error":true,"error":{"message":"rate limit exceeded"}}'
    err = adapter.extract_error(stdout, b"")
    assert err is not None
    assert err.kind is EngineErrorKind.RATE_LIMIT


def test_cursor_extract_error_always_none() -> None:
    adapter = CursorAdapter()
    stdout = b'{"type":"error","message":"boom"}\n'
    assert adapter.extract_error(stdout, b"") is None


def test_passthrough_extract_error_always_none() -> None:
    adapter = get_output_adapter("unknown")
    stdout = b'{"type":"error","message":"boom"}\n'
    assert adapter.extract_error(stdout, b"") is None
