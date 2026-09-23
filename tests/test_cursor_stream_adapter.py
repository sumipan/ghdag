"""Tests for CursorStreamAdapter (stream-json → result / session_id / error).

Fixtures are slimmed from real
`agent -p --force --trust --output-format stream-json --stream-partial-output`
captures (2026-09-09): success / empty result / auth failure (stderr only).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from ghdag.core.capabilities import LLMCapabilities
from ghdag.llm.adapters import get_output_adapter
from ghdag.llm.adapters.cursor_stream import CursorStreamAdapter
from ghdag.llm.engines import TextResult, call

_FIXTURES = Path(__file__).parent / "fixtures"
_STREAM_SUCCESS = (_FIXTURES / "cursor_stream_success.jsonl").read_bytes()
_STREAM_EMPTY = (_FIXTURES / "cursor_stream_empty.jsonl").read_bytes()
_AUTH_STDOUT = (_FIXTURES / "cursor_stream_auth_fail.stdout").read_bytes()
_AUTH_STDERR = (_FIXTURES / "cursor_stream_auth_fail.stderr").read_bytes()
_LEGACY_JSON = (_FIXTURES / "cursor_legacy_json.json").read_bytes()
_RETRIABLE_STDERR = (_FIXTURES / "cursor_stream_retriable_error.stderr").read_bytes()
_RETRIABLE_RECONNECT_STDERR = (
    _FIXTURES / "cursor_stream_retriable_error_reconnect.stderr"
).read_bytes()


class TestCursorStreamAdapterExtraction:
    def test_extract_result_text_from_stream_success(self):
        adapter = CursorStreamAdapter()
        assert adapter.extract_result_text(_STREAM_SUCCESS, b"") == b"pong"

    def test_stream_result_matches_legacy_json_path(self):
        adapter = CursorStreamAdapter()
        stream_text = adapter.extract_result_text(_STREAM_SUCCESS, b"")
        legacy_text = adapter.extract_result_text(_LEGACY_JSON, b"")
        assert stream_text == legacy_text == b"pong"

    def test_extract_result_text_empty_result(self):
        adapter = CursorStreamAdapter()
        assert adapter.extract_result_text(_STREAM_EMPTY, b"") == b""

    def test_extract_session_id_from_stream(self):
        adapter = CursorStreamAdapter()
        assert adapter.extract_session_id(_STREAM_SUCCESS, b"") == (
            "5e7ca003-6637-4c42-b168-e2a4c05f1c8c"
        )

    def test_text_result_session_id_from_stream(self):
        adapter = CursorStreamAdapter()
        sid = adapter.extract_session_id(_STREAM_SUCCESS, b"")
        from ghdag.llm.engines import LLMResult

        raw = LLMResult(stdout=_STREAM_SUCCESS.decode(), stderr="", returncode=0, session_id=sid)
        assert TextResult(body="pong", success=True, raw=raw).session_id == sid

    def test_is_terminal_result_event(self):
        adapter = CursorStreamAdapter()
        assert adapter.is_terminal_result_event({"type": "result", "result": "pong"})
        assert not adapter.is_terminal_result_event({"type": "assistant"})
        assert not adapter.is_terminal_result_event({"type": "thinking"})

    def test_auth_fail_classifies_failure(self):
        adapter = CursorStreamAdapter()
        classified = adapter.classify_failure(1, _AUTH_STDOUT, _AUTH_STDERR)
        assert classified is not None

    def test_get_output_adapter_returns_cursor_stream(self):
        adapter = get_output_adapter("cursor")
        assert isinstance(adapter, CursorStreamAdapter)


class TestCursorStreamExtractError:
    def test_ac1_retriable_error_single_line(self):
        from ghdag.core.ports.output import EngineErrorKind

        adapter = CursorStreamAdapter()
        err = adapter.extract_error(b"", _RETRIABLE_STDERR)
        assert err is not None
        assert err.kind == EngineErrorKind.RATE_LIMIT
        assert err.retryable is True
        assert err.message == "RetriableError: [resource_exhausted] Error"

    def test_ac1_retriable_error_reconnect_fixture(self):
        from ghdag.core.ports.output import EngineErrorKind

        adapter = CursorStreamAdapter()
        err = adapter.extract_error(b"", _RETRIABLE_RECONNECT_STDERR)
        assert err is not None
        assert err.kind == EngineErrorKind.RATE_LIMIT
        assert err.retryable is True
        assert err.message == "RetriableError: [resource_exhausted] Error"

    def test_ac1b_unavailable_is_capacity(self):
        from ghdag.core.ports.output import EngineErrorKind

        adapter = CursorStreamAdapter()
        err = adapter.extract_error(b"", b"RetriableError: [unavailable] Error\n")
        assert err is not None
        assert err.kind == EngineErrorKind.CAPACITY
        assert err.retryable is True

    def test_ac1b_connection_stalled_is_capacity(self):
        from ghdag.core.ports.output import EngineErrorKind

        adapter = CursorStreamAdapter()
        err = adapter.extract_error(b"", b"RetriableError: Connection stalled repeatedly\n")
        assert err is not None
        assert err.kind == EngineErrorKind.CAPACITY
        assert err.retryable is True

    def test_ac1c_unauthenticated_returns_none(self):
        adapter = CursorStreamAdapter()
        assert adapter.extract_error(b"", b"RetriableError: [unauthenticated] Error\n") is None

    def test_ac1c_non_retriable_error_returns_none(self):
        adapter = CursorStreamAdapter()
        assert (
            adapter.extract_error(
                b"", b"NonRetriableError: Service Unavailable Service Unavailable\n"
            )
            is None
        )

    def test_ac1c_empty_stderr_returns_none(self):
        adapter = CursorStreamAdapter()
        assert adapter.extract_error(b"", b"") is None

    def test_ac1c_invalid_utf8_stderr_returns_none(self):
        adapter = CursorStreamAdapter()
        assert adapter.extract_error(b"", b"\xff\xfe") is None

    def test_ac2_success_stdout_ignores_retriable_stderr(self):
        adapter = CursorStreamAdapter()
        err = adapter.extract_error(_STREAM_SUCCESS, _RETRIABLE_STDERR)
        assert err is None

    def test_ac2_assistant_only_stdout_uses_stderr_path(self):
        from ghdag.core.ports.output import EngineErrorKind

        assistant_only = (
            b'{"type": "assistant", "message": {"role": "assistant", "content": []}}\n'
        )
        adapter = CursorStreamAdapter()
        err = adapter.extract_error(assistant_only, _RETRIABLE_STDERR)
        assert err is not None
        assert err.kind == EngineErrorKind.RATE_LIMIT

    def test_ac3_result_payload_rate_limit(self):
        from ghdag.core.ports.output import EngineErrorKind

        payload = b'{"type": "result", "subtype": "error", "is_error": true, "result": "Rate limit exceeded"}\n'
        adapter = CursorStreamAdapter()
        err = adapter.extract_error(payload, b"")
        assert err is not None
        assert err.kind == EngineErrorKind.RATE_LIMIT
        assert err.retryable is True

    def test_ac3_result_payload_capacity(self):
        from ghdag.core.ports.output import EngineErrorKind

        payload = b'{"type": "result", "subtype": "error", "is_error": true, "result": "Model is at capacity"}\n'
        adapter = CursorStreamAdapter()
        err = adapter.extract_error(payload, b"")
        assert err is not None
        assert err.kind == EngineErrorKind.CAPACITY
        assert err.retryable is True

    def test_ac3_result_payload_unknown(self):
        from ghdag.core.ports.output import EngineErrorKind

        payload = b'{"type": "result", "subtype": "error", "is_error": true, "result": "something broke"}\n'
        adapter = CursorStreamAdapter()
        err = adapter.extract_error(payload, b"")
        assert err is not None
        assert err.kind == EngineErrorKind.UNKNOWN
        assert err.retryable is False

    def test_ac5_classify_common_failure_not_quota_exhausted(self):
        from ghdag.llm.adapters.failure_classification import classify_common_failure

        result = classify_common_failure(
            "cursor", b"", b"RetriableError: [resource_exhausted] Error"
        )
        assert result is None


class TestCursorStreamCapability:
    @patch("ghdag.llm.engines.subprocess.run")
    def test_cursor_stream_no_longer_raises(self, mock_run: MagicMock):
        mock_run.return_value = MagicMock(
            stdout=_STREAM_SUCCESS.decode(),
            stderr="",
            returncode=0,
        )
        result = call(
            "hello",
            engine="cursor",
            capabilities=LLMCapabilities(stream=True),
        )
        assert result.ok
        assert result.stdout == "pong"
        assert result.session_id == "5e7ca003-6637-4c42-b168-e2a4c05f1c8c"
        cmd = mock_run.call_args[0][0]
        assert "--output-format" in cmd
        fmt_idx = cmd.index("--output-format")
        assert cmd[fmt_idx + 1] == "stream-json"
        assert "--stream-partial-output" in cmd
