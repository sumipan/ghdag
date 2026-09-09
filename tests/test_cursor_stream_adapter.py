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
