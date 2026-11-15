"""Tests for DAG task cancel via jobs/cancel + jobs/running control files."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from ghdag.core.vocabulary import DONE_CANCELLED
from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DefaultHooks
from ghdag.dag.models import DagConfig


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


def _run_engine_in_thread(engine: DagEngine) -> threading.Thread:
    t = threading.Thread(target=engine.run, daemon=True)
    t.start()
    return t


def _stop_engine(engine: DagEngine, thread: threading.Thread) -> None:
    engine._shutdown = True
    thread.join()


class TestTaskCancel:

    def test_cancel_supports_legacy_hooks_without_cancel_callback(
        self, tmp_path, monkeypatch,
    ):
        """Cancel still works when legacy hooks omit on_task_cancelled."""

        class LegacyHooks:
            def on_task_start(self, uuid, task):
                pass

            def check_rejected(self, result_path):
                return False

            def check_pipeline_status(self, result_path):
                return None

        config = _make_config(
            tmp_path,
            [{"uuid": "uuid-a", "command": "sleep 60", "depends": []}],
            kill_grace=1.0,
        )
        started = threading.Event()

        class StartedLegacyHooks(LegacyHooks):
            def on_task_start(self, uuid, task):
                if uuid == "uuid-a":
                    started.set()

        engine = DagEngine(config, StartedLegacyHooks())
        cancelled = threading.Event()
        from ghdag.dag import task_launcher

        real_mark_done = task_launcher.state_mark_done

        def mark_done_and_notify(exec_done_dir, uuid, status):
            real_mark_done(exec_done_dir, uuid, status)
            if uuid == "uuid-a" and status == DONE_CANCELLED:
                cancelled.set()

        monkeypatch.setattr(task_launcher, "state_mark_done", mark_done_and_notify)

        t = _run_engine_in_thread(engine)
        started.wait()
        assert "uuid-a" in engine._launcher._running

        cancel_path = _queue_dir(config) / "cancel" / "uuid-a"
        cancel_path.parent.mkdir(parents=True, exist_ok=True)
        cancel_path.write_text("", encoding="utf-8")
        cancelled.wait()

        assert _read_done(config, "uuid-a") == DONE_CANCELLED
        _stop_engine(engine, t)


    def test_default_hooks_on_task_cancelled_noop(self):
        """DefaultHooks.on_task_cancelled is a no-op without raising."""
        hooks = DefaultHooks()
        from ghdag.dag.models import Task

        task = Task(uuid="u1", command="echo", depends=[])
        hooks.on_task_cancelled("u1", task)
