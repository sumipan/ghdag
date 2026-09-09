"""Multi-engine DAG progress events (cursor / codex → jobs/events).

Uses slimmed real fixtures from tests/fixtures/ (Issue #2967).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.core.command import render_exec_command
from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig
from ghdag.dag.task_launcher import _engine_emits_stream_events

_FIXTURES = Path(__file__).parent / "fixtures"
_CURSOR_STREAM = (_FIXTURES / "cursor_stream_success.jsonl").read_text(encoding="utf-8")
_CODEX_JSONL = (_FIXTURES / "codex_jsonl_success.jsonl").read_text(encoding="utf-8")
_CURSOR_LEGACY = (_FIXTURES / "cursor_legacy_json.json").read_text(encoding="utf-8")


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


class TestCursorCodexExtraArgs:
    def test_cursor_dag_default_is_stream_json(self):
        spec = ENGINE_SPECS["cursor"]
        assert "--output-format" in spec.extra_args
        idx = spec.extra_args.index("--output-format")
        assert spec.extra_args[idx + 1] == "stream-json"
        assert "--stream-partial-output" in spec.extra_args
        cmd = render_exec_command(spec, order_path="jobs/o.md", model=None)
        assert "--output-format stream-json" in cmd
        assert "--stream-partial-output" in cmd
        assert "--output-format json" not in cmd

    def test_codex_dag_default_keeps_json(self):
        spec = ENGINE_SPECS["codex"]
        assert "--json" in spec.extra_args
        cmd = render_exec_command(spec, order_path="jobs/o.md", model=None)
        assert "--json" in cmd


class TestEngineEmitsStreamEvents:
    def test_claude_cursor_codex_emit(self):
        assert _engine_emits_stream_events("claude")
        assert _engine_emits_stream_events("cursor")
        assert _engine_emits_stream_events("codex")

    def test_gemini_shell_do_not_emit(self):
        assert not _engine_emits_stream_events("gemini")
        assert not _engine_emits_stream_events("shell")
        assert not _engine_emits_stream_events(None)


class TestMultiEngineDagEvents:
    def test_cursor_task_writes_events_and_compatible_result(self, tmp_path):
        result_file = tmp_path / "result.md"
        fixture = tmp_path / "fixture.jsonl"
        fixture.write_text(_CURSOR_STREAM, encoding="utf-8")
        config = _make_config(
            tmp_path,
            [
                {
                    "uuid": "evt-cursor",
                    "engine": "cursor",
                    # `: ...` は bash no-op。launcher が stream 可否判定に使うフラグを埋め込む
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

        events_path = tmp_path / "jobs" / "events" / "evt-cursor.jsonl"
        assert events_path.is_file()
        assert events_path.read_text(encoding="utf-8") == _CURSOR_STREAM
        assert result_file.read_text(encoding="utf-8") == "pong"
        hooks.on_task_progress.assert_called()
        recorded = engine._launcher._session_store.lookup("evt-cursor")
        assert recorded is not None
        assert recorded.session_id == "5e7ca003-6637-4c42-b168-e2a4c05f1c8c"

    def test_codex_task_writes_events_and_compatible_result(self, tmp_path):
        result_file = tmp_path / "result.md"
        fixture = tmp_path / "fixture.jsonl"
        fixture.write_text(_CODEX_JSONL, encoding="utf-8")
        config = _make_config(
            tmp_path,
            [
                {
                    "uuid": "evt-codex",
                    "engine": "codex",
                    "command": f"cat {fixture} # --json",
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

        events_path = tmp_path / "jobs" / "events" / "evt-codex.jsonl"
        assert events_path.is_file()
        assert events_path.read_text(encoding="utf-8") == _CODEX_JSONL
        assert result_file.read_text(encoding="utf-8") == "pong"
        hooks.on_task_progress.assert_called()
        recorded = engine._launcher._session_store.lookup("evt-codex")
        assert recorded is not None
        assert recorded.session_id == "01a085df-dac8-7060-8452-030f649c1b94"


class TestStreamFallback:
    def test_cursor_without_print_falls_back(self, tmp_path):
        """cursor コマンドに -p/--print が無いとき一括読み + stream_fallback。"""
        result_file = tmp_path / "result.md"
        fixture = tmp_path / "fixture.json"
        fixture.write_text(_CURSOR_LEGACY, encoding="utf-8")
        config = _make_config(
            tmp_path,
            [
                {
                    "uuid": "evt-fallback",
                    "engine": "cursor",
                    # -p 無し・stream-json 無し → stream 不可
                    "command": f"cat {fixture}",
                    "depends": [],
                    "result_path": str(result_file),
                    "annotations": {},
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
        done = tmp_path / "jobs" / "done" / "evt-fallback"
        while time.time() < deadline and not done.exists():
            time.sleep(0.05)
        engine._shutdown = True
        t.join(timeout=3.0)

        events_path = tmp_path / "jobs" / "events" / "evt-fallback.jsonl"
        assert not events_path.exists()
        assert result_file.read_text(encoding="utf-8") == "pong"
        # annotations に stream_fallback が記録される
        # TaskLauncher が annotations を更新するため exec 再読 or launcher 経由で確認
        # DagEngine が保持する tasks を見る
        task = engine._tasks["evt-fallback"]
        assert task.annotations.get("stream_fallback") in {True, "true"}
        hooks.on_task_progress.assert_not_called()

    def test_codex_without_json_falls_back(self, tmp_path):
        result_file = tmp_path / "result.md"
        # --json 無し → 一括読み。stdout は JSONL 風でもフラグ判定は command 文字列のみ
        payload = json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": "plain"},
            }
        )
        config = _make_config(
            tmp_path,
            [
                {
                    "uuid": "evt-codex-fb",
                    "engine": "codex",
                    "command": f"printf '%s\\n' '{payload}'",
                    "depends": [],
                    "result_path": str(result_file),
                    "annotations": {},
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
        done = tmp_path / "jobs" / "done" / "evt-codex-fb"
        while time.time() < deadline and not done.exists():
            time.sleep(0.05)
        engine._shutdown = True
        t.join(timeout=3.0)

        events_path = tmp_path / "jobs" / "events" / "evt-codex-fb.jsonl"
        assert not events_path.exists()
        assert result_file.read_text(encoding="utf-8") == "plain"
        task = engine._tasks["evt-codex-fb"]
        assert task.annotations.get("stream_fallback") in {True, "true"}
