from __future__ import annotations

from ghdag.core.ports.output import EngineErrorKind
from ghdag.llm.adapters import get_output_adapter
from ghdag.llm.adapters.claude_json import ClaudeJsonAdapter
from ghdag.llm.adapters.codex import CodexAdapter


def test_codex_extract_error_rate_limit_event() -> None:
    adapter = CodexAdapter()
    stdout = b'{"type":"turn.failed","error":"Rate limit exceeded"}\n'
    err = adapter.extract_error(stdout, b'')
    assert err is not None
    assert err.kind is EngineErrorKind.RATE_LIMIT
    assert err.retryable is True

def test_claude_extract_error_normal_json_returns_none() -> None:
    adapter = ClaudeJsonAdapter()
    stdout = b'{"result":"hello","type":"result"}'
    assert adapter.extract_error(stdout, b'') is None

def test_claude_rate_limit_is_not_quota_exhausted() -> None:
    adapter = ClaudeJsonAdapter()
    stdout = b'{"is_error":true,"error":{"message":"rate limit exceeded"}}'
    err = adapter.extract_error(stdout, b'')
    assert err is not None
    assert err.kind is EngineErrorKind.RATE_LIMIT

def test_passthrough_extract_error_always_none() -> None:
    adapter = get_output_adapter('unknown')
    stdout = b'{"type":"error","message":"boom"}\n'
    assert adapter.extract_error(stdout, b'') is None
_CODEX_USAGE_LIMIT_STDOUT = b'{"type":"thread.started","thread_id":"01a08690-c49f-7e12-98aa-dffc0d6a3d3f"}\n{"type":"turn.started"}\n{"type":"error","message":"You\'ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at Sep 10th, 2026 2:13 AM."}\n{"type":"turn.failed","error":{"message":"You\'ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at Sep 10th, 2026 2:13 AM."}}\n'

def test_codex_usage_limit_is_quota_exhausted_with_local_resume_at() -> None:
    """2026-09-09 measured: usage limit returned by ChatGPT-account-auth codex (nexus #2961 cp2)."""
    adapter = CodexAdapter()
    err = adapter.extract_error(_CODEX_USAGE_LIMIT_STDOUT, b'')
    assert err is not None
    assert err.kind is EngineErrorKind.QUOTA_EXHAUSTED
    assert err.retryable is False
    assert err.resume_at is not None
    assert (err.resume_at.year, err.resume_at.month, err.resume_at.day) == (2026, 9, 10)
    assert (err.resume_at.hour, err.resume_at.minute) == (2, 13)
    assert err.resume_at.tzinfo is not None
