"""Tests for default-pause behavior when _parse_reset_at() returns None."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ghdag.core.models.metrics import FailureClass
from ghdag.core.ports.output import EngineErrorKind
from ghdag.llm.adapters.claude_json import ClaudeJsonAdapter
from ghdag.llm.adapters.codex import CodexAdapter
from ghdag.llm.adapters.cursor_stream import CursorStreamAdapter
from ghdag.llm.adapters.failure_classification import (
    QUOTA_DEFAULT_PAUSE_SECONDS,
    classify_common_failure,
)

FIXED_NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)


class TestCodexClassifyErrorDefaultPause:
    """codex _classify_error falls back to default pause when no reset_at in message."""

    def test_quota_exhausted_no_reset_at_uses_default_pause(self):
        from ghdag.llm.adapters.codex import _classify_error

        kind, retryable, resume_at = _classify_error(
            "quota exhausted, please wait", FIXED_NOW
        )

        assert kind == EngineErrorKind.QUOTA_EXHAUSTED
        assert retryable is False
        assert resume_at is not None
        expected = FIXED_NOW + timedelta(seconds=QUOTA_DEFAULT_PAUSE_SECONDS)
        assert resume_at == expected

    def test_quota_exhausted_with_reset_at_uses_parsed_time(self):
        from ghdag.llm.adapters.codex import _classify_error

        message = "quota exhausted, resets at 2026-09-21T00:00:00+00:00"
        kind, retryable, resume_at = _classify_error(message, FIXED_NOW)

        assert kind == EngineErrorKind.QUOTA_EXHAUSTED
        assert retryable is False
        assert resume_at is not None
        assert resume_at == datetime(2026, 9, 21, 0, 0, 0, tzinfo=timezone.utc)


class TestClaudeJsonClassifyErrorDefaultPause:
    """claude_json _classify_error falls back to default pause when no reset_at in message."""

    def test_quota_exhausted_no_reset_at_uses_default_pause(self):
        from ghdag.llm.adapters.claude_json import _classify_error

        kind, retryable, resume_at = _classify_error(
            "quota exhausted", FIXED_NOW
        )

        assert kind == EngineErrorKind.QUOTA_EXHAUSTED
        assert retryable is False
        assert resume_at is not None
        expected = FIXED_NOW + timedelta(seconds=QUOTA_DEFAULT_PAUSE_SECONDS)
        assert resume_at == expected


# ---------------------------------------------------------------------------
# classify_common_failure: environment / auth rules and real-output fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "failure_classification"


class TestEnvironmentErrorRules:

    def test_empty_binary_keeps_legacy_behavior(self):
        assert (
            classify_common_failure("", b"", b"foo: No such file or directory")
            == FailureClass.ENGINE_ENVIRONMENT_ERROR
        )
        assert (
            classify_common_failure("", b"", b"open x: Permission denied")
            == FailureClass.ENGINE_ENVIRONMENT_ERROR
        )


class TestAuthErrorRules:

    @pytest.mark.parametrize("text", ["authored by x", "coauthor", "authorize the app later"])
    def test_non_auth_words(self, text):
        assert classify_common_failure("claude", text.encode(), b"", returncode=1) is None

    def test_quota_still_takes_precedence_over_auth(self):
        assert (
            classify_common_failure("codex", b"", b"You've hit your usage limit (unauthorized)")
            == FailureClass.QUOTA_EXHAUSTED
        )


def test_fixtures_have_no_cjk():
    ranges = ((0x3000, 0x30FF), (0x3400, 0x9FFF), (0xFF00, 0xFFEF))
    cjk = re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in ranges) + "]")
    for path in FIXTURE_DIR.glob("*.json"):
        assert not cjk.search(path.read_text(encoding="utf-8")), path


def test_codex_capacity_classifies_as_engine_error() -> None:
    """Refs sumipan/nexus#6602: capacity error event maps to ENGINE_ERROR via extract_error."""
    adapter = CodexAdapter()
    stdout = (
        b'{"type":"error","message":"Selected model is at capacity. '
        b'Please try a different model."}\n'
    )
    assert adapter.classify_failure(1, stdout, b"") is FailureClass.ENGINE_ERROR


def test_claude_capacity_classifies_as_engine_error() -> None:
    """Refs sumipan/nexus#6602: overloaded maps to ENGINE_ERROR via extract_error."""
    adapter = ClaudeJsonAdapter()
    stdout = b'{"is_error":true,"error":{"message":"overloaded"}}'
    assert adapter.classify_failure(1, stdout, b"") is FailureClass.ENGINE_ERROR


def test_cursor_capacity_classifies_as_engine_error() -> None:
    """Refs sumipan/nexus#6602: result error capacity maps to ENGINE_ERROR via extract_error."""
    adapter = CursorStreamAdapter()
    stdout = (
        b'{"type":"result","subtype":"error","is_error":true,'
        b'"result":"Selected model is at capacity."}\n'
    )
    assert adapter.classify_failure(1, stdout, b"") is FailureClass.ENGINE_ERROR


def test_failure_class_values_unchanged():
    assert [m.name for m in FailureClass] == [
        "TIMEOUT",
        "REJECTED",
        "ENGINE_ERROR",
        "QUOTA_EXHAUSTED",
        "AUTH",
        "ENGINE_ENVIRONMENT_ERROR",
        "INTERACTIVE_PROMPT",
        "PROCESS_ERROR",
        "PIPELINE_FAILED",
        "EMPTY_RESULT",
        "FANOUT_CHILD_FAILED",
        "FANOUT_PARSE_FAILED",
        "DEP_FAILED",
        "UNKNOWN_FAILURE",
    ]
