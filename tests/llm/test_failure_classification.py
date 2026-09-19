"""Tests for default-pause behavior when _parse_reset_at() returns None."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ghdag.core.ports.output import EngineErrorKind
from ghdag.llm.adapters.failure_classification import QUOTA_DEFAULT_PAUSE_SECONDS

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

    def test_quota_exhausted_with_reset_at_uses_parsed_time(self):
        from ghdag.llm.adapters.claude_json import _classify_error

        message = "quota exhausted, resets at 2026-09-21T06:00:00+00:00"
        kind, retryable, resume_at = _classify_error(message, FIXED_NOW)

        assert kind == EngineErrorKind.QUOTA_EXHAUSTED
        assert retryable is False
        assert resume_at is not None
        assert resume_at == datetime(2026, 9, 21, 6, 0, 0, tzinfo=timezone.utc)
