"""Tests for DAG task cancel via jobs/cancel + jobs/running control files."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.core.vocabulary import DONE_CANCELLED, DONE_DEP_FAILED
from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DefaultHooks
from ghdag.dag.models import DagConfig
from ghdag.dag.state import is_done


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
        poll_interval=0.1,
        launch_stagger=0.0,
        lock_file=str(tmp_path / "lock"),
        kill_grace=1.0,
        task_timeout=None,
    )
    defaults.update(overrides)
    return DagConfig(**defaults)


def _queue_dir(config: DagConfig) -> Path:
    return Path(config.exec_done_dir).parent


def _read_done(config: DagConfig, uuid: str) -> str:
    return (Path(config.exec_done_dir) / uuid).read_text(encoding="utf-8").strip()


class TestTaskCancel:
    def test_cancel_running_sleep_marks_cancelled(self, tmp_path):
        """実行中 sleep を cancel → kill_grace 内に CANCELLED done マーカー。"""
        config = _make_config(
            tmp_path,
            [{"uuid": "uuid-a", "command": "sleep 60", "depends": []}],
            kill_grace=1.0,
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        engine = DagEngine(config, hooks)

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        deadline = time.time() + 5.0
        while time.time() < deadline and "uuid-a" not in engine._launcher._running:
            time.sleep(0.05)
        assert "uuid-a" in engine._launcher._running

        running_path = _queue_dir(config) / "running" / "uuid-a.json"
        assert running_path.is_file()
        meta = json.loads(running_path.read_text(encoding="utf-8"))
        assert isinstance(meta["pid"], int)
        assert isinstance(meta["pgid"], int)
        assert "started_at" in meta
        assert "has_resume" in meta

        cancel_path = _queue_dir(config) / "cancel" / "uuid-a"
        cancel_path.parent.mkdir(parents=True, exist_ok=True)
        cancel_path.write_text("", encoding="utf-8")

        done_deadline = time.time() + 5.0
        while time.time() < done_deadline and not is_done(config.exec_done_dir, "uuid-a"):
            time.sleep(0.05)

        engine._shutdown = True
        t.join(timeout=3.0)

        assert is_done(config.exec_done_dir, "uuid-a")
        assert _read_done(config, "uuid-a") == DONE_CANCELLED
        assert not running_path.exists()
        assert not cancel_path.exists()
        hooks.on_task_cancelled.assert_called_once()
        call_uuid, call_task = hooks.on_task_cancelled.call_args[0]
        assert call_uuid == "uuid-a"
        assert call_task.uuid == "uuid-a"

    def test_cancel_propagates_dep_failed(self, tmp_path):
        """キャンセルされた親に依存する子は DEP_FAILED。"""
        config = _make_config(
            tmp_path,
            [
                {"uuid": "uuid-a", "command": "sleep 60", "depends": []},
                {"uuid": "uuid-b", "command": "echo child", "depends": ["uuid-a"]},
            ],
            kill_grace=1.0,
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        engine = DagEngine(config, hooks)

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        deadline = time.time() + 5.0
        while time.time() < deadline and "uuid-a" not in engine._launcher._running:
            time.sleep(0.05)
        assert "uuid-a" in engine._launcher._running

        cancel_path = _queue_dir(config) / "cancel" / "uuid-a"
        cancel_path.parent.mkdir(parents=True, exist_ok=True)
        cancel_path.write_text("", encoding="utf-8")

        done_deadline = time.time() + 8.0
        while time.time() < done_deadline:
            if is_done(config.exec_done_dir, "uuid-a") and is_done(config.exec_done_dir, "uuid-b"):
                break
            time.sleep(0.05)

        engine._shutdown = True
        t.join(timeout=3.0)

        assert _read_done(config, "uuid-a") == DONE_CANCELLED
        assert _read_done(config, "uuid-b") == DONE_DEP_FAILED
        hooks.on_task_dep_failed.assert_called()

    def test_running_file_removed_on_success(self, tmp_path):
        """正常終了でも jobs/running/<uuid>.json が消える。"""
        config = _make_config(
            tmp_path,
            [{"uuid": "uuid-a", "command": "sleep 0.2", "depends": []}],
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        engine = DagEngine(config, hooks)

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        deadline = time.time() + 5.0
        saw_running = False
        running_path = _queue_dir(config) / "running" / "uuid-a.json"
        while time.time() < deadline:
            if running_path.is_file():
                saw_running = True
            if is_done(config.exec_done_dir, "uuid-a"):
                break
            time.sleep(0.05)

        engine._shutdown = True
        t.join(timeout=3.0)

        assert saw_running
        assert is_done(config.exec_done_dir, "uuid-a")
        assert _read_done(config, "uuid-a") == "0"
        assert not running_path.exists()

    def test_running_file_removed_on_timeout(self, tmp_path):
        """タイムアウト終了でも jobs/running/<uuid>.json が消える。"""
        config = _make_config(
            tmp_path,
            [{"uuid": "uuid-a", "command": "sleep 60", "depends": []}],
            task_timeout=1.0,
            kill_grace=1.0,
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        engine = DagEngine(config, hooks)

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        deadline = time.time() + 8.0
        running_path = _queue_dir(config) / "running" / "uuid-a.json"
        while time.time() < deadline and not is_done(config.exec_done_dir, "uuid-a"):
            time.sleep(0.05)

        engine._shutdown = True
        t.join(timeout=3.0)

        assert is_done(config.exec_done_dir, "uuid-a")
        assert _read_done(config, "uuid-a") == "TIMEOUT"
        assert not running_path.exists()

    def test_default_hooks_on_task_cancelled_noop(self):
        """DefaultHooks.on_task_cancelled は例外なく noop。"""
        hooks = DefaultHooks()
        from ghdag.dag.models import Task

        task = Task(uuid="u1", command="echo", depends=[])
        hooks.on_task_cancelled("u1", task)
