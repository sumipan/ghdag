"""cursor stream-json から完結 assistant 本文だけを抽出する（nexus #3255）。

途中実況（tool_call 前の完結メッセージ）が result / call_text に混入しないこと、
および DAG result / events の契約を表形式で検証する。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ghdag.core.capabilities import LLMCapabilities
from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig
from ghdag.llm.adapters.cursor_stream import (
    CursorStreamAdapter,
    extract_final_assistant_text,
)
from ghdag.llm.engines import LLMResult

_FIXTURES = Path(__file__).parent / "fixtures"
_FINAL_MESSAGE = (_FIXTURES / "cursor_stream_final_message.jsonl").read_text(encoding="utf-8")

_A = "あんどぅーとして朝の秘書フローを進めます。まず Asana とカレンダーを取りにいきますね。"
_B = (
    "<@USER> 夜だねー。日曜の19時すぎ。カレンダー上の予定はひととおり通り過ぎたあたり。"
    "終日は休肝日。\n\n持ち越しは粗大ゴミやOlive連携、あたり。"
    "てろんとした隙間も少し残しておこうねー。"
)


def _assistant(text: str, *, model_call_id: str | None = None) -> dict:
    obj: dict = {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        "session_id": "sess",
    }
    if model_call_id is not None:
        obj["model_call_id"] = model_call_id
    return obj


def _tool_call(subtype: str = "started") -> dict:
    return {"type": "tool_call", "subtype": subtype, "call_id": "t1"}


def _result(text: str) -> dict:
    return {"type": "result", "subtype": "success", "result": text, "session_id": "sess"}


def _jsonl(*events: dict) -> str:
    return "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)


def _partials(text: str, size: int = 3) -> list[dict]:
    return [_assistant(text[i : i + size]) for i in range(0, len(text), size)]


class TestExtractFinalAssistantText:
    def test_ac1_returns_only_b_after_tool_call(self):
        stdout = _FINAL_MESSAGE.encode("utf-8")
        assert extract_final_assistant_text(stdout) == _B

    def test_ac2_complete_without_model_call_id_matching_partials(self):
        events = [
            *_partials("hello world", 4),
            _assistant("hello world"),  # no model_call_id
            _result("hello world"),
        ]
        assert extract_final_assistant_text(_jsonl(*events).encode()) == "hello world"

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
            (_jsonl(_assistant("only-partial")).encode(), None),
            (
                _jsonl(
                    *_partials("abc", 1),
                    # no complete event
                    _result("abc"),
                ).encode(),
                None,
            ),
            (b"not json\n{broken", None),
            (_jsonl({"type": "assistant", "message": {"content": []}}).encode(), None),
        ],
        ids=[
            "last_of_multiple_completes",
            "empty",
            "single_partial_only",
            "partials_without_complete",
            "broken_jsonl",
            "empty_content",
        ],
    )
    def test_ac3_boundaries(self, stdout: bytes, expected: str | None):
        assert extract_final_assistant_text(stdout) == expected

    def test_ac4_does_not_join_partials_across_tool_call(self):
        # チャンクが互いに一致しない文言を使い、途中片が誤って完結判定されないようにする
        events = [
            *_partials("AAAA", 2),
            _tool_call("started"),
            _tool_call("completed"),
            *_partials("WXYZ", 2),
            # tool_call をまたいだ連結 "AAAAWXYZ" ではなく、以降の "WXYZ" だけ
            _assistant("WXYZ"),
            _result("AAAAWXYZ"),
        ]
        assert extract_final_assistant_text(_jsonl(*events).encode()) == "WXYZ"

    def test_ac4_ignores_non_text_content_and_empty_text(self):
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
        assert extract_final_assistant_text(_jsonl(*events).encode()) == "ok"

    def test_ac9_anonymized_evening_fixture(self):
        text = extract_final_assistant_text(_FINAL_MESSAGE.encode())
        assert text is not None
        assert "夜だねー" in text
        assert "秘書フローを進めます" not in text


class TestCursorStreamAdapterFinalMessage:
    def test_ac5_adapter_prefers_final_assistant(self):
        adapter = CursorStreamAdapter()
        assert adapter.extract_result_text(_FINAL_MESSAGE.encode(), b"") == _B.encode()

    def test_ac5_fallback_typed_result(self):
        adapter = CursorStreamAdapter()
        raw = _jsonl(_result("x")).encode()
        assert adapter.extract_result_text(raw, b"") == b"x"

    def test_ac5_fallback_single_json(self):
        adapter = CursorStreamAdapter()
        raw = json.dumps({"result": "x"}).encode()
        assert adapter.extract_result_text(raw, b"") == b"x"

    def test_ac5_fallback_non_json(self):
        adapter = CursorStreamAdapter()
        assert adapter.extract_result_text(b"plain text", b"") == b"plain text"


class TestLLMResultValidateCursor:
    def test_ac6_cursor_stream_replaces_stdout_with_b(self):
        caps = LLMCapabilities(stream=True)
        result = LLMResult(stdout=_FINAL_MESSAGE, stderr="", returncode=0)
        validated = result.validate(caps, engine="cursor")
        assert validated.stdout == _B
        # call_text 相当: validate 後の stdout を再度 adapter に渡しても B のまま
        adapter = CursorStreamAdapter()
        body = adapter.extract_result_text(validated.stdout.encode(), b"").decode()
        assert body == _B

    def test_ac7_claude_keeps_last_result(self):
        caps = LLMCapabilities(stream=True)
        stream = _jsonl(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "mid"}]}},
            _result("final-claude"),
        )
        result = LLMResult(stdout=stream, stderr="", returncode=0)
        assert result.validate(caps, engine="claude").stdout == "final-claude"

    def test_ac7_codex_keeps_raw_jsonl(self):
        caps = LLMCapabilities(stream=True)
        raw = '{"type":"item.completed"}\n{"type":"result","result":"x"}\n'
        result = LLMResult(stdout=raw, stderr="", returncode=0)
        assert result.validate(caps, engine="codex").stdout == raw


def _make_config(tmp_path: Path, records: list[dict]) -> DagConfig:
    jobs = tmp_path / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    exec_jsonl = jobs / "exec.jsonl"
    exec_jsonl.write_text(
        "".join(json.dumps(r) + "\n" for r in records),
        encoding="utf-8",
    )
    return DagConfig(
        exec_jsonl_path=str(exec_jsonl),
        exec_done_dir=str(jobs / "done"),
        poll_interval=0.05,
        launch_stagger=0.0,
        lock_file=str(tmp_path / "lock"),
        kill_grace=1.0,
        task_timeout=None,
        cwd=str(tmp_path),
    )


class TestDagResultUsesFinalMessage:
    def test_ac8_result_is_b_events_preserve_jsonl(self, tmp_path: Path):
        result_file = tmp_path / "result.md"
        fixture = tmp_path / "fixture.jsonl"
        fixture.write_text(_FINAL_MESSAGE, encoding="utf-8")
        config = _make_config(
            tmp_path,
            [
                {
                    "uuid": "evt-final",
                    "engine": "cursor",
                    "command": f"cat {fixture} # -p --output-format stream-json",
                    "depends": [],
                    "result_path": str(result_file),
                }
            ],
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        engine = DagEngine(config, hooks)
        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        deadline = time.time() + 5.0
        while time.time() < deadline and not result_file.exists():
            time.sleep(0.05)
        engine._shutdown = True
        t.join(timeout=3.0)

        events_path = tmp_path / "jobs" / "events" / "evt-final.jsonl"
        assert events_path.is_file()
        assert events_path.read_text(encoding="utf-8") == _FINAL_MESSAGE
        assert result_file.read_text(encoding="utf-8") == _B
