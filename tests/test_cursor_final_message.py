"""Rebuild assistant turns from cursor stream-json with blank-line separators (nexus #3260).

Confirm each turn at tool_call boundaries, that the deduplicated full text
matches across result / call(route=[("claude", None)]) / DAG result, and that events remain raw JSONL.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghdag.core.capabilities import Permission
from ghdag.llm.adapters.cursor_stream import (
    CursorStreamAdapter,
    reconstruct_assistant_turns,
)
from ghdag.llm.engines import call

_FIXTURES = Path(__file__).parent / "fixtures"
_FINAL_MESSAGE = (_FIXTURES / "cursor_stream_final_message.jsonl").read_text(encoding="utf-8")

# Synthetic English fixture text (host persona names scrubbed; see nexus #3338).
_A = (
    "Acting as persona-1, I will start the morning assistant flow. "
    "First I will fetch tasks and calendar."
)
_B = (
    "<@USER> Evening update. It is past 19:00 on Sunday. "
    "Calendar items are mostly done. All-day is marked rest.\n\n"
    "Carry-overs are bulk trash and Olive sync. I will leave a quiet gap too."
)
_JOINED = f"{_A}\n\n{_B}"
# Contiguous host persona literals must not appear in this repo (#3338 / #3333 AC-4).
# Encoded as \u escapes so denylist scanners do not match this test source itself.
_HOST_PERSONA_LEAKS = (
    "\u3042\u3093\u3069\u3045\u30fc",  # nickname
    "\u5b89\u85e4\u745e\u7a42",  # full name
    "\u30cf\u30cb",  # other host nickname
)


def _assistant(
    text: str,
    *,
    model_call_id: str | None = None,
    timestamp_ms: int | None = None,
) -> dict:
    obj: dict = {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        "session_id": "sess",
    }
    if model_call_id is not None:
        obj["model_call_id"] = model_call_id
    if timestamp_ms is not None:
        obj["timestamp_ms"] = timestamp_ms
    return obj


def _result(text: str) -> dict:
    return {"type": "result", "subtype": "success", "result": text, "session_id": "sess"}


def _jsonl(*events: dict) -> str:
    return "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)


def _partials(text: str, size: int = 3) -> list[dict]:
    # Deltas from --stream-partial-output carry timestamp_ms; complete texts do not.
    return [
        _assistant(text[i : i + size], timestamp_ms=i + 1)
        for i in range(0, len(text), size)
    ]


def _deltas(*pieces: str) -> list[dict]:
    return [_assistant(p, timestamp_ms=i + 1) for i, p in enumerate(pieces)]


class TestReconstructAssistantTurns:
    def test_reconstruct_joins_turns_with_blank_line(self):
        stdout = _FINAL_MESSAGE.encode("utf-8")
        assert reconstruct_assistant_turns(stdout) == _JOINED

    @pytest.mark.parametrize(
        "stdout,expected",
        [
            (
                _jsonl(
                    _assistant("first", model_call_id="c1"),
                    _assistant("second", model_call_id="c2"),
                    _result("firstsecond"),
                ).encode(),
                "second",
            ),
            (b"", None),
            (_jsonl(_assistant("only-partial")).encode(), "only-partial"),
            (
                _jsonl(
                    *_partials("abc", 1),
                    _result("abc"),
                ).encode(),
                "abc",
            ),
            (
                _jsonl(_assistant("solo", model_call_id="c1"), _result("solo")).encode(),
                "solo",
            ),
            (b"not json\n{broken", None),
            (_jsonl({"type": "assistant", "message": {"content": []}}).encode(), None),
            (b"[1, 2, 3]\n", None),
        ],
        ids=[
            "last_of_multiple_completes_same_turn",
            "empty",
            "single_partial_only",
            "partials_without_complete",
            "complete_only_no_deltas",
            "broken_jsonl",
            "empty_content",
            "non_dict_json",
        ],
    )
    def test_reconstruct_parametrized_edge_cases(self, stdout: bytes, expected: str | None):
        assert reconstruct_assistant_turns(stdout) == expected

    def test_repro_fixture_repeated_leading_delta(self):
        stdout = (_FIXTURES / "cursor_stream_repro.jsonl").read_bytes()
        assert reconstruct_assistant_turns(stdout) == "llamas are great."

    def test_repeated_delta_equal_to_concat_is_not_complete(self):
        events = [*_deltas("ab", "ab", "c"), _result("ababc")]
        assert reconstruct_assistant_turns(_jsonl(*events).encode()) == "ababc"

    def test_reconstruct_skips_non_text_and_empty_assistant_chunks(self):
        events = [
            {
                "type": "assistant",
                "model_call_id": "c1",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "image", "url": "x"},
                        {"type": "text", "text": ""},
                    ],
                },
            },
            *_partials("ok", 1),
            _assistant("ok"),
            _result("ok"),
        ]
        assert reconstruct_assistant_turns(_jsonl(*events).encode()) == "ok"

    def test_scrubbed_fixture_has_no_host_persona_literals(self):
        for token in _HOST_PERSONA_LEAKS:
            assert token not in _FINAL_MESSAGE
            assert token not in _A
            assert token not in _B
            assert token not in _JOINED


class TestCursorStreamAdapterFinalMessage:
    def test_adapter_extract_uses_reconstructed_turns(self):
        adapter = CursorStreamAdapter()
        assert adapter.extract_result_text(_FINAL_MESSAGE.encode(), b"") == _JOINED.encode()

    def test_repro_fixture_result_text(self):
        adapter = CursorStreamAdapter()
        raw = (_FIXTURES / "cursor_stream_repro.jsonl").read_bytes()
        assert adapter.extract_result_text(raw, b"") == b"llamas are great."

    def test_adapter_extract_falls_back_to_result_event(self):
        adapter = CursorStreamAdapter()
        raw = _jsonl(_result("x")).encode()
        assert adapter.extract_result_text(raw, b"") == b"x"

    def test_adapter_extract_falls_back_to_single_json_object(self):
        adapter = CursorStreamAdapter()
        raw = json.dumps({"result": "x"}).encode()
        assert adapter.extract_result_text(raw, b"") == b"x"

    def test_adapter_extract_falls_back_to_raw_bytes(self):
        adapter = CursorStreamAdapter()
        assert adapter.extract_result_text(b"plain text", b"") == b"plain text"


class TestCallStreamNormalization:
    @pytest.fixture(autouse=True)
    def _stall_guard_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GHDAG_ENGINE_STALL_GUARD", "0")

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_cursor_stream_body_becomes_joined_turns(self, mock_run):
        mock_run.return_value = MagicMock(stdout=_FINAL_MESSAGE, stderr="", returncode=0)
        result = call("p", permission=Permission(stream=True), route=[("cursor", None)])
        assert result.body == _JOINED
        assert result.raw_stdout == _FINAL_MESSAGE

    @patch("ghdag.llm.engines.subprocess.run")
    def test_call_codex_stream_preserves_raw_stdout(self, mock_run):
        raw = '{"type":"item.completed"}\n{"type":"result","result":"x"}\n'
        mock_run.return_value = MagicMock(stdout=raw, stderr="", returncode=0)
        result = call("p", permission=Permission(stream=True), route=[("codex", None)])
        assert result.stdout == raw
