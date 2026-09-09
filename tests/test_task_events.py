"""Tests for DAG claude progress events (stream-json → jobs/events).

Fixtures are slimmed from real `claude -p --output-format stream-json --verbose`
captures (success / tool-use / error_during_execution). Empty-result line uses the
same result-row schema with an empty `result` string.
"""

from __future__ import annotations

import io
import json
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.core.command import render_exec_command
from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.dag._util import _stdout_line_reader, _stdout_reader
from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DefaultHooks
from ghdag.dag.models import DagConfig
from ghdag.llm.adapters.claude_json import ClaudeJsonAdapter, extract_stream_result
from ghdag.llm.engines import TextResult, _extract_stream_result
from ghdag.ui.server import _build_snapshot, _latest_progress

# --- Real-shape fixtures (captured 2026-09-09, slimmed) ---------------------

_STREAM_SUCCESS = "\n".join(
    [
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "9664201d-a9c9-43d6-b070-6d7ee4183d55",
                "model": "claude-opus-5[1m]",
                "cwd": "/tmp/fixture",
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "pong"}],
                },
                "session_id": "9664201d-a9c9-43d6-b070-6d7ee4183d55",
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "pong",
                "session_id": "9664201d-a9c9-43d6-b070-6d7ee4183d55",
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 4,
                    "cache_read_input_tokens": 10010,
                    "cache_creation_input_tokens": 17148,
                },
                "total_cost_usd": 0.177553,
                "duration_ms": 1672,
            },
            ensure_ascii=False,
        ),
    ]
) + "\n"

_STREAM_TOOLS = "\n".join(
    [
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "c8bbdd82-576c-41bf-92fb-75c72fe9ae0b",
                "model": "claude-opus-5[1m]",
                "cwd": "/tmp/fixture",
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_01UE3cspKJNioxwjwy4K8Y6p",
                            "name": "Read",
                            "input": {
                                "file_path": "/tmp/fixture/pyproject.toml",
                                "limit": 20,
                            },
                        }
                    ],
                },
                "session_id": "c8bbdd82-576c-41bf-92fb-75c72fe9ae0b",
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_01UE3cspKJNioxwjwy4K8Y6p",
                            "content": "(tool output omitted)",
                        }
                    ],
                },
                "session_id": "c8bbdd82-576c-41bf-92fb-75c72fe9ae0b",
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "ghdag"}],
                },
                "session_id": "c8bbdd82-576c-41bf-92fb-75c72fe9ae0b",
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "ghdag",
                "session_id": "c8bbdd82-576c-41bf-92fb-75c72fe9ae0b",
                "usage": {
                    "input_tokens": 4,
                    "output_tokens": 253,
                    "cache_read_input_tokens": 39874,
                    "cache_creation_input_tokens": 15061,
                },
                "total_cost_usd": 0.177887,
                "duration_ms": 4939,
            },
            ensure_ascii=False,
        ),
    ]
) + "\n"

# Real capture: invalid --resume → type=result subtype=error_during_execution
_STREAM_ERROR = (
    '{"type":"result","subtype":"error_during_execution","duration_ms":0,'
    '"duration_api_ms":0,"is_error":true,"num_turns":0,"stop_reason":null,'
    '"session_id":"00000000-0000-0000-0000-000000000000","total_cost_usd":0,'
    '"usage":{"input_tokens":0,"cache_creation_input_tokens":0,'
    '"cache_read_input_tokens":0,"output_tokens":0},'
    '"errors":["No conversation found with session ID: '
    '00000000-0000-0000-0000-000000000000"],'
    '"uuid":"err-fixture-uuid"}\n'
)

# Empty result text using the same result-row schema as success captures
_STREAM_EMPTY = (
    '{"type":"system","subtype":"init","session_id":"empty-sess","cwd":"/tmp"}\n'
    '{"type":"result","subtype":"success","is_error":false,"result":"",'
    '"session_id":"empty-sess","usage":{"input_tokens":1,"output_tokens":0},'
    '"total_cost_usd":0.0,"duration_ms":10}\n'
)

_LEGACY_JSON_RESULT = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "pong",
        "session_id": "9664201d-a9c9-43d6-b070-6d7ee4183d55",
        "usage": {"input_tokens": 2, "output_tokens": 4},
        "total_cost_usd": 0.001,
    }
)


def _make_config(tmp_path: Path, records: list[dict], **overrides) -> DagConfig:
    jobs = tmp_path / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    exec_jsonl = jobs / "exec.jsonl"
    exec_jsonl.write_text(
        "".join(json.dumps(r) + "\n" for r in records),
        encoding="utf-8",
    )
    defaults = dict(
        exec_jsonl_path=str(exec_jsonl),
        exec_done_dir=str(jobs / "done"),
        poll_interval=0.05,
        launch_stagger=0.0,
        lock_file=str(tmp_path / "lock"),
        kill_grace=1.0,
        task_timeout=None,
        cwd=str(tmp_path),
    )
    defaults.update(overrides)
    return DagConfig(**defaults)


class TestExtractStreamResultShared:
    def test_extract_stream_result_matches_engines_alias(self):
        assert extract_stream_result(_STREAM_SUCCESS) == "pong"
        assert _extract_stream_result(_STREAM_SUCCESS) == "pong"

    def test_extract_stream_result_empty_string(self):
        assert extract_stream_result(_STREAM_EMPTY) == ""

    def test_legacy_single_json_still_extracts_result_text(self):
        adapter = ClaudeJsonAdapter()
        assert adapter.extract_result_text(_LEGACY_JSON_RESULT.encode(), b"") == b"pong"

    def test_stream_json_result_text_matches_legacy_json_path(self):
        """stream-json 経路の result テキストは従来 json 経路と同じ本文になる。"""
        adapter = ClaudeJsonAdapter()
        stream_text = adapter.extract_result_text(_STREAM_SUCCESS.encode(), b"")
        legacy_text = adapter.extract_result_text(_LEGACY_JSON_RESULT.encode(), b"")
        assert stream_text == legacy_text == b"pong"

    def test_stream_json_session_id_for_text_result(self):
        adapter = ClaudeJsonAdapter()
        sid = adapter.extract_session_id(_STREAM_SUCCESS.encode(), b"")
        assert sid == "9664201d-a9c9-43d6-b070-6d7ee4183d55"
        # TextResult.session_id は raw.session_id 経由
        from ghdag.llm.engines import LLMResult

        raw = LLMResult(stdout=_STREAM_SUCCESS, stderr="", returncode=0, session_id=sid)
        assert TextResult(body="pong", success=True, raw=raw).session_id == sid

    def test_stream_error_extracts_engine_error(self):
        adapter = ClaudeJsonAdapter()
        err = adapter.extract_error(_STREAM_ERROR.encode(), b"")
        assert err is not None
        assert "No conversation found" in err.message


class TestStdoutLineReader:
    def test_line_reader_appends_events_and_fills_buf(self, tmp_path):
        events = tmp_path / "events" / "u1.jsonl"
        # Slow-ish line producer so the reader can observe growth
        script = (
            "import sys, time\n"
            f"data = { _STREAM_TOOLS!r }\n"
            "for line in data.splitlines(True):\n"
            "    sys.stdout.write(line)\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.02)\n"
        )
        proc = subprocess.Popen(
            ["python3", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        buf = io.BytesIO()
        seen: list[dict] = []

        def on_line(event: dict) -> None:
            seen.append(event)

        t = threading.Thread(
            target=_stdout_line_reader,
            args=(proc, buf, events, on_line),
            daemon=True,
        )
        t.start()
        # While still running, events file should grow
        deadline = time.time() + 3.0
        grew = False
        while time.time() < deadline:
            if events.is_file() and events.stat().st_size > 0:
                grew = True
                break
            time.sleep(0.01)
        assert grew
        proc.wait(timeout=5)
        t.join(timeout=2)
        assert events.read_text(encoding="utf-8") == _STREAM_TOOLS
        assert buf.getvalue().decode("utf-8") == _STREAM_TOOLS
        assert any(e.get("type") == "assistant" for e in seen)
        assert any(
            (e.get("message") or {}).get("content", [{}])[0].get("name") == "Read"
            for e in seen
            if e.get("type") == "assistant"
        )


class TestClaudeDagEvents:
    def test_claude_extra_args_are_stream_json(self):
        spec = ENGINE_SPECS["claude"]
        assert spec.extra_args == ("--output-format", "stream-json", "--verbose")
        cmd = render_exec_command(spec, order_path="jobs/o.md", model=None)
        assert "--output-format stream-json" in cmd
        assert "--verbose" in cmd
        assert "--output-format json" not in cmd

    def test_claude_task_writes_events_and_compatible_result(self, tmp_path):
        result_file = tmp_path / "result.md"
        fixture = tmp_path / "fixture.jsonl"
        fixture.write_text(_STREAM_SUCCESS, encoding="utf-8")
        config = _make_config(
            tmp_path,
            [
                {
                    "uuid": "evt-claude",
                    "engine": "claude",
                    "command": f"cat {fixture}",
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

        events_path = tmp_path / "jobs" / "events" / "evt-claude.jsonl"
        assert events_path.is_file()
        assert events_path.read_text(encoding="utf-8") == _STREAM_SUCCESS
        assert result_file.read_text(encoding="utf-8") == "pong"
        hooks.on_task_progress.assert_called()
        # session recorded
        recorded = engine._launcher._session_store.lookup("evt-claude")
        assert recorded is not None
        assert recorded.session_id == "9664201d-a9c9-43d6-b070-6d7ee4183d55"

    def test_non_claude_engine_does_not_write_events(self, tmp_path):
        result_file = tmp_path / "result.md"
        config = _make_config(
            tmp_path,
            [
                {
                    "uuid": "evt-shell",
                    "engine": "shell",
                    "command": "printf 'hello\\n'",
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
        while time.time() < deadline and not (tmp_path / "jobs" / "done" / "evt-shell").exists():
            time.sleep(0.05)
        engine._shutdown = True
        t.join(timeout=3.0)

        events_path = tmp_path / "jobs" / "events" / "evt-shell.jsonl"
        assert not events_path.exists()
        assert result_file.read_text(encoding="utf-8") == "hello\n"
        hooks.on_task_progress.assert_not_called()

    def test_legacy_hooks_without_progress_do_not_break(self, tmp_path):
        class LegacyHooks:
            def on_task_start(self, uuid, task):
                pass

            def on_task_success(self, uuid, task, metrics):
                pass

            def on_task_failure(self, uuid, task, returncode, stderr_text, metrics):
                pass

            def on_task_rejected(self, uuid, task, retry_depth, is_final, metrics):
                pass

            def on_task_dep_failed(self, uuid, task, failed_dep):
                pass

            def on_task_empty_result(self, uuid, task, stderr_text, metrics):
                pass

            def on_shutdown(self, signum):
                pass

            def check_rejected(self, result_path):
                return False

            def check_pipeline_status(self, result_path):
                return None

            def check_promote_target(self, result_path):
                return None

        result_file = tmp_path / "result.md"
        fixture = tmp_path / "fixture.jsonl"
        fixture.write_text(_STREAM_SUCCESS, encoding="utf-8")
        config = _make_config(
            tmp_path,
            [
                {
                    "uuid": "evt-legacy",
                    "engine": "claude",
                    "command": f"cat {fixture}",
                    "depends": [],
                    "result_path": str(result_file),
                }
            ],
        )
        engine = DagEngine(config, LegacyHooks())
        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        deadline = time.time() + 5.0
        while time.time() < deadline and not result_file.exists():
            time.sleep(0.05)
        engine._shutdown = True
        t.join(timeout=3.0)
        assert result_file.read_text(encoding="utf-8") == "pong"

    def test_default_hooks_on_task_progress_is_noop(self):
        hooks = DefaultHooks()
        hooks.on_task_progress("u", {"type": "assistant"})


class TestUiProgressFromEvents:
    def test_latest_progress_from_tool_use(self, tmp_path):
        events = tmp_path / "jobs" / "events"
        events.mkdir(parents=True)
        (events / "u1.jsonl").write_text(_STREAM_TOOLS, encoding="utf-8")
        # Force a mid-file latest meaningful event: rewrite stopping before result
        lines = _STREAM_TOOLS.strip().splitlines()
        (events / "u1.jsonl").write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
        prog = _latest_progress(tmp_path, "u1")
        assert prog is not None
        assert prog.get("tool") == "Read"
        assert prog.get("path") == "/tmp/fixture/pyproject.toml"

    def test_build_snapshot_includes_progress(self, tmp_path):
        jobs = tmp_path / "jobs"
        jobs.mkdir()
        (jobs / "exec.jsonl").write_text(
            json.dumps(
                {
                    "uuid": "snap-1",
                    "command": "claude -p --output-format stream-json < o.md",
                    "depends": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (jobs / "done").mkdir()
        events = jobs / "events"
        events.mkdir()
        (events / "snap-1.jsonl").write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": "working on it"}],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        rows = _build_snapshot(tmp_path, max_visible=30)
        assert rows
        matched = [r for r in rows if r.get("uuid") == "snap-1"]
        assert matched
        assert matched[0].get("progress", {}).get("assistant_text") == "working on it"


def test_chunk_reader_still_available():
    """非 stream 経路用のチャンク読みが残っていること。"""
    assert callable(_stdout_reader)
