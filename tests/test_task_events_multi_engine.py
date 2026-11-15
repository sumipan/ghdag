"""Multi-engine DAG progress events (cursor / codex → jobs/events).

Uses slimmed real fixtures from tests/fixtures/ (Issue #2967).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig
from ghdag.dag.task_launcher import _engine_emits_stream_events


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


def _run_engine_until_success(engine: DagEngine, hooks: MagicMock) -> None:
    finished = threading.Event()

    def on_success(*_args, **_kwargs) -> None:
        finished.set()

    hooks.on_task_success.side_effect = on_success
    t = threading.Thread(target=engine.run, daemon=True)
    t.start()
    finished.wait()
    engine._shutdown = True
    t.join()


class TestEngineEmitsStreamEvents:
    def test_claude_cursor_codex_emit(self):
        assert _engine_emits_stream_events("claude")
        assert _engine_emits_stream_events("cursor")
        assert _engine_emits_stream_events("codex")

    def test_gemini_shell_do_not_emit(self):
        assert not _engine_emits_stream_events("gemini")
        assert not _engine_emits_stream_events("shell")
        assert not _engine_emits_stream_events(None)


class TestStreamFallback:
    def test_codex_without_json_falls_back(self, tmp_path):
        result_file = tmp_path / "result.md"
        # without --json → bulk read; even JSONL-like stdout: eligibility uses command string only
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
        _run_engine_until_success(engine, hooks)

        events_path = tmp_path / "jobs" / "events" / "evt-codex-fb.jsonl"
        assert not events_path.exists()
        assert result_file.read_text(encoding="utf-8") == "plain"
        task = engine._tasks["evt-codex-fb"]
        assert task.annotations.get("stream_fallback") in {True, "true"}
