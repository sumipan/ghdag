"""Tests for DAG task cancel via jobs/cancel + jobs/running control files."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.core.vocabulary import DONE_CANCELLED, DONE_DEP_FAILED
from ghdag.dag.engine import DagEngine
from ghdag.dag.hooks import DefaultHooks
from ghdag.dag.models import DagConfig
from ghdag.io.done import is_done


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


def _read_done_if_present(config: DagConfig, uuid: str) -> str | None:
    path = Path(config.exec_done_dir) / uuid
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip()


class _FakeClock:
    def __init__(self, mono: float = 10_000.0, wall: float = 1_700_000_000.0) -> None:
        self._mono = mono
        self._wall = wall

    def monotonic(self) -> float:
        val = self._mono
        self._mono += 0.6
        return val

    def time(self) -> float:
        return self._wall


def _spin_until(predicate) -> None:
    while not predicate():
        pass


def _run_engine_in_thread(engine: DagEngine) -> threading.Thread:
    t = threading.Thread(target=engine.run, daemon=True)
    t.start()
    return t


def _stop_engine(engine: DagEngine, thread: threading.Thread) -> None:
    engine._shutdown = True
    thread.join()


def _wait_task_start(hooks: MagicMock, uuid: str = "uuid-a") -> threading.Event:
    started = threading.Event()

    def on_start(task_uuid, task):
        if task_uuid == uuid:
            started.set()

    hooks.on_task_start.side_effect = on_start
    return started


class TestTaskCancel:
    def test_cancel_running_sleep_marks_cancelled(self, tmp_path, monkeypatch):
        """Cancel an in-flight sleep → CANCELLED done marker within kill_grace."""
        config = _make_config(
            tmp_path,
            [{"uuid": "uuid-a", "command": "sleep 60", "depends": []}],
            kill_grace=1.0,
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        cancelled = threading.Event()

        def on_cancelled(task_uuid, task):
            cancelled.set()

        hooks.on_task_cancelled.side_effect = on_cancelled
        started = _wait_task_start(hooks)
        engine = DagEngine(config, hooks)
        real_replace = os.replace
        replace_targets = []

        def recording_replace(src, dst):
            replace_targets.append(Path(dst))
            real_replace(src, dst)

        monkeypatch.setattr("ghdag.dag.task_launcher.os.replace", recording_replace)

        t = _run_engine_in_thread(engine)
        started.wait()
        assert "uuid-a" in engine._launcher._running

        running_path = _queue_dir(config) / "running" / "uuid-a.json"
        assert running_path.is_file()
        meta = json.loads(running_path.read_text(encoding="utf-8"))
        assert isinstance(meta["pid"], int)
        assert isinstance(meta["pgid"], int)
        assert "started_at" in meta
        assert "has_resume" in meta
        assert running_path in replace_targets

        cancel_path = _queue_dir(config) / "cancel" / "uuid-a"
        cancel_path.parent.mkdir(parents=True, exist_ok=True)
        cancel_path.write_text("", encoding="utf-8")

        cancelled.wait()
        _stop_engine(engine, t)

        assert is_done(config.exec_done_dir, "uuid-a")
        assert _read_done(config, "uuid-a") == DONE_CANCELLED
        assert not running_path.exists()
        assert not cancel_path.exists()
        hooks.on_task_cancelled.assert_called_once()
        call_uuid, call_task = hooks.on_task_cancelled.call_args[0]
        assert call_uuid == "uuid-a"
        assert call_task.uuid == "uuid-a"

    def test_cancel_supports_legacy_hooks_without_cancel_callback(self, tmp_path):
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

        t = _run_engine_in_thread(engine)
        started.wait()
        assert "uuid-a" in engine._launcher._running

        cancel_path = _queue_dir(config) / "cancel" / "uuid-a"
        cancel_path.parent.mkdir(parents=True, exist_ok=True)
        cancel_path.write_text("", encoding="utf-8")
        _spin_until(lambda: _read_done_if_present(config, "uuid-a") == DONE_CANCELLED)

        assert _read_done(config, "uuid-a") == DONE_CANCELLED
        _stop_engine(engine, t)

    def test_cancel_propagates_dep_failed(self, tmp_path):
        """Child depending on a cancelled parent becomes DEP_FAILED."""
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
        parent_done = threading.Event()
        child_dep_failed = threading.Event()

        def on_cancelled(task_uuid, task):
            if task_uuid == "uuid-a":
                parent_done.set()

        def on_dep_failed(task_uuid, task, failed_dep):
            if task_uuid == "uuid-b":
                child_dep_failed.set()

        hooks.on_task_cancelled.side_effect = on_cancelled
        hooks.on_task_dep_failed.side_effect = on_dep_failed
        started = _wait_task_start(hooks)
        engine = DagEngine(config, hooks)

        t = _run_engine_in_thread(engine)
        started.wait()
        assert "uuid-a" in engine._launcher._running

        cancel_path = _queue_dir(config) / "cancel" / "uuid-a"
        cancel_path.parent.mkdir(parents=True, exist_ok=True)
        cancel_path.write_text("", encoding="utf-8")

        parent_done.wait()
        child_dep_failed.wait()
        _stop_engine(engine, t)

        assert _read_done(config, "uuid-a") == DONE_CANCELLED
        assert _read_done(config, "uuid-b") == DONE_DEP_FAILED
        hooks.on_task_dep_failed.assert_called()

    def test_running_file_removed_on_success(self, tmp_path):
        """jobs/running/<uuid>.json is cleared on normal completion too."""
        config = _make_config(
            tmp_path,
            [{"uuid": "uuid-a", "command": "sleep 0.2", "depends": []}],
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        saw_running = False
        success = threading.Event()

        def on_start(task_uuid, task):
            nonlocal saw_running
            running_path = _queue_dir(config) / "running" / "uuid-a.json"
            if running_path.is_file():
                saw_running = True

        def on_success(task_uuid, task, metrics):
            success.set()

        hooks.on_task_start.side_effect = on_start
        hooks.on_task_success.side_effect = on_success
        engine = DagEngine(config, hooks)
        running_path = _queue_dir(config) / "running" / "uuid-a.json"

        t = _run_engine_in_thread(engine)
        success.wait()
        _stop_engine(engine, t)

        assert saw_running
        assert is_done(config.exec_done_dir, "uuid-a")
        assert _read_done(config, "uuid-a") == "0"
        assert not running_path.exists()

    def test_running_file_removed_on_timeout(self, tmp_path, monkeypatch):
        """jobs/running/<uuid>.json is cleared on timeout exit too."""
        clock = _FakeClock()
        monkeypatch.setattr("ghdag.dag.task_launcher.time", clock)
        config = _make_config(
            tmp_path,
            [{"uuid": "uuid-a", "command": "sleep 60", "depends": []}],
            task_timeout=1.0,
            kill_grace=1.0,
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        hooks.check_pipeline_status.return_value = None
        timed_out = threading.Event()

        def on_failure(task_uuid, task, returncode, stderr_text, metrics):
            if task_uuid == "uuid-a":
                timed_out.set()

        hooks.on_task_failure.side_effect = on_failure
        engine = DagEngine(config, hooks)
        running_path = _queue_dir(config) / "running" / "uuid-a.json"

        t = _run_engine_in_thread(engine)
        timed_out.wait()
        _stop_engine(engine, t)

        assert is_done(config.exec_done_dir, "uuid-a")
        assert _read_done(config, "uuid-a") == "TIMEOUT"
        assert not running_path.exists()

    def test_default_hooks_on_task_cancelled_noop(self):
        """DefaultHooks.on_task_cancelled is a no-op without raising."""
        hooks = DefaultHooks()
        from ghdag.dag.models import Task

        task = Task(uuid="u1", command="echo", depends=[])
        hooks.on_task_cancelled("u1", task)
