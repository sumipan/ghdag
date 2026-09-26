"""Tests for default-pause behavior when _parse_reset_at() returns None."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ghdag.core.models.metrics import FailureClass
from ghdag.core.ports.output import EngineErrorKind
from ghdag.llm.adapters import get_output_adapter
from ghdag.llm.adapters.cursor import CursorAdapter
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

    def test_quota_exhausted_with_reset_at_uses_parsed_time(self):
        from ghdag.llm.adapters.claude_json import _classify_error

        message = "quota exhausted, resets at 2026-09-21T06:00:00+00:00"
        kind, retryable, resume_at = _classify_error(message, FIXED_NOW)

        assert kind == EngineErrorKind.QUOTA_EXHAUSTED
        assert retryable is False
        assert resume_at is not None
        assert resume_at == datetime(2026, 9, 21, 6, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# classify_common_failure: environment / auth rules and real-output fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "failure_classification"
ENGINES = ("claude", "cursor", "codex")


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class TestEnvironmentErrorRules:
    def test_cursor_cli_command_not_found_with_127(self):
        assert (
            classify_common_failure(
                "cursor", b"", b"bash: line 1: agent: command not found", returncode=127
            )
            == FailureClass.ENGINE_ENVIRONMENT_ERROR
        )

    def test_cli_command_not_found_without_returncode(self):
        assert (
            classify_common_failure("cursor", b"", b"bash: line 1: agent: command not found")
            == FailureClass.ENGINE_ENVIRONMENT_ERROR
        )

    def test_engine_name_still_matches(self):
        assert (
            classify_common_failure("codex", b"", b"/bin/sh: codex: Permission denied")
            == FailureClass.ENGINE_ENVIRONMENT_ERROR
        )

    def test_quoted_name_matches(self):
        assert (
            classify_common_failure("cursor", b"", b"exec: 'agent': command not found")
            == FailureClass.ENGINE_ENVIRONMENT_ERROR
        )

    def test_python_errno_format(self):
        assert (
            classify_common_failure(
                "cursor", b"", b"[Errno 2] No such file or directory: 'agent'"
            )
            == FailureClass.ENGINE_ENVIRONMENT_ERROR
        )
        assert (
            classify_common_failure("claude", b"", b"[Errno 13] Permission denied: 'claude'")
            == FailureClass.ENGINE_ENVIRONMENT_ERROR
        )

    def test_returncode_127_command_not_found_other_binary(self):
        assert (
            classify_common_failure("claude", b"", b"env: node: command not found", returncode=127)
            == FailureClass.ENGINE_ENVIRONMENT_ERROR
        )

    def test_command_not_found_without_127_and_other_binary_is_not_env(self):
        assert (
            classify_common_failure("claude", b"", b"env: node: command not found", returncode=1)
            is None
        )

    def test_generic_agent_word_with_permission_denied_is_not_env(self):
        assert (
            classify_common_failure(
                "cursor", b"The agent could not open /tmp/x: permission denied", b"", returncode=1
            )
            is None
        )

    def test_name_as_substring_is_not_env(self):
        assert (
            classify_common_failure("cursor", b"", b"useragent: command not found", returncode=1)
            is None
        )

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
    def test_author_is_not_auth(self):
        assert (
            classify_common_failure("cursor", b"", b"error: author field is required", returncode=1)
            is None
        )

    @pytest.mark.parametrize(
        "text",
        [
            "auth error: 401",
            "Authentication failed",
            "missing Authorization header",
            "UNAUTHENTICATED: request had invalid credentials",
            "401 Unauthorized",
            "403 Forbidden",
            "OAuth session expired",
            "Invalid API key",
            "Not logged in",
        ],
    )
    def test_auth_tokens(self, text):
        assert classify_common_failure("claude", b"", text.encode(), returncode=1) == FailureClass.AUTH

    @pytest.mark.parametrize("text", ["authored by x", "coauthor", "authorize the app later"])
    def test_non_auth_words(self, text):
        assert classify_common_failure("claude", text.encode(), b"", returncode=1) is None

    def test_quota_still_takes_precedence_over_auth(self):
        assert (
            classify_common_failure("codex", b"", b"You've hit your usage limit (unauthorized)")
            == FailureClass.QUOTA_EXHAUSTED
        )


@pytest.mark.parametrize("engine", ENGINES)
class TestRealOutputFixtures:
    def test_binary_missing(self, engine):
        data = _load_fixture(f"{engine}_binary_missing.json")
        stdout = data["stdout"].encode()
        stderr = data["stderr"].encode()
        assert (
            classify_common_failure(engine, stdout, stderr, returncode=data["returncode"])
            == FailureClass.ENGINE_ENVIRONMENT_ERROR
        )
        adapter = get_output_adapter(engine)
        assert (
            adapter.classify_failure(data["returncode"], stdout, stderr)
            == FailureClass.ENGINE_ENVIRONMENT_ERROR
        )

    def test_auth_fail(self, engine):
        data = _load_fixture(f"{engine}_auth_fail.json")
        stdout = data["stdout"].encode()
        stderr = data["stderr"].encode()
        assert (
            classify_common_failure(engine, stdout, stderr, returncode=data["returncode"])
            == FailureClass.AUTH
        )
        adapter = get_output_adapter(engine)
        assert adapter.classify_failure(data["returncode"], stdout, stderr) == FailureClass.AUTH


def test_fixtures_have_no_cjk():
    cjk = re.compile(r"[\u3000-\u30ff\u3400-\u9fff\uff00-\uffef]")
    for path in FIXTURE_DIR.glob("*.json"):
        assert not cjk.search(path.read_text(encoding="utf-8")), path


def test_legacy_cursor_adapter_classifies_binary_missing():
    data = _load_fixture("cursor_binary_missing.json")
    assert (
        CursorAdapter().classify_failure(
            data["returncode"], data["stdout"].encode(), data["stderr"].encode()
        )
        == FailureClass.ENGINE_ENVIRONMENT_ERROR
    )


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
