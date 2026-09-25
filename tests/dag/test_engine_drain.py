"""AC-1, AC-2e: DagEngine drain behavior on SIGTERM."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig
from ghdag.dag.state import is_done


def _make_engine(tmp_path: Path, task_timeout: float | None = 30.0) -> DagEngine:
    jobs = tmp_path / "jobs"
    done = jobs / "done"
    done.mkdir(parents=True)
    config = DagConfig(
        exec_jsonl_path=str(jobs / "exec.jsonl"),
        exec_done_dir=str(done),
        poll_interval=0.05,
        launch_stagger=0.0,
        lock_file=str(tmp_path / "lock"),
        task_timeout=task_timeout,
        kill_grace=0.5,
    )
    hooks = MagicMock()
    return DagEngine(config, hooks=hooks)


def _write_exec_jsonl(jobs: Path, uuid: str, command: str) -> None:
    path = jobs / "exec.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"uuid": uuid, "command": command, "depends": []}) + "\n",
        encoding="utf-8",
    )


def _wait_until(predicate, *, timeout: float = 10.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
UUID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class TestDrainNormalCompletion:
    """AC-1: drain waits for running tasks, done marker is success."""

    def test_drain_waits_for_running_task(self, tmp_path):
        jobs = tmp_path / "jobs"
        done = jobs / "done"
        engine = _make_engine(tmp_path, task_timeout=30.0)
        result_path = str(jobs / f"result-{UUID_A}.md")
        Path(result_path).parent.mkdir(parents=True, exist_ok=True)
        _write_exec_jsonl(jobs, UUID_A, f"echo ok > {result_path}")

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        try:
            assert _wait_until(lambda: engine._launcher.is_running(UUID_A), timeout=5.0)

            engine._launcher.mark_interrupted_all()
            engine._draining = True

            assert _wait_until(lambda: not engine._launcher.is_running(UUID_A), timeout=10.0)
            assert _wait_until(lambda: not t.is_alive(), timeout=5.0)

            assert is_done(str(done), UUID_A)
            done_content = (done / UUID_A).read_text(encoding="utf-8").strip()
            assert done_content == "0"
        finally:
            engine._shutdown = True
            t.join(timeout=5)


class TestDrainTimeout:
    """AC-2: drain deadline exceeded — done marker not written, running file has interrupted_at."""

    def test_drain_deadline_exceeded_leaves_interrupted_at(self, tmp_path):
        jobs = tmp_path / "jobs"
        engine = _make_engine(tmp_path, task_timeout=30.0)
        result_path = str(jobs / f"result-{UUID_A}.md")
        Path(result_path).parent.mkdir(parents=True, exist_ok=True)
        _write_exec_jsonl(jobs, UUID_A, f"sleep 30 && echo ok > {result_path}")

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        try:
            assert _wait_until(lambda: engine._launcher.is_running(UUID_A), timeout=5.0)

            engine._launcher.mark_interrupted_all()
            engine._draining = True
            engine._drain_deadline = time.monotonic() + 0.2

            assert _wait_until(lambda: not t.is_alive(), timeout=10.0)

            done_dir = jobs / "done"
            assert not is_done(str(done_dir), UUID_A)

            running_file = jobs / "running" / f"{UUID_A}.json"
            assert running_file.exists()
            data = json.loads(running_file.read_text(encoding="utf-8"))
            assert "interrupted_at" in data
        finally:
            engine._shutdown = True
            t.join(timeout=5)
            for rt in list(engine._launcher._running.values()):
                try:
                    os.killpg(rt.proc.pid, 9)
                except Exception:
                    pass


class TestDrainSecondSignal:
    """AC-2e: second signal during drain causes immediate shutdown without waiting."""

    def test_second_signal_causes_immediate_shutdown(self, tmp_path):
        jobs = tmp_path / "jobs"
        engine = _make_engine(tmp_path, task_timeout=30.0)
        result_path = str(jobs / f"result-{UUID_A}.md")
        Path(result_path).parent.mkdir(parents=True, exist_ok=True)
        _write_exec_jsonl(jobs, UUID_A, f"sleep 30 && echo ok > {result_path}")

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        try:
            assert _wait_until(lambda: engine._launcher.is_running(UUID_A), timeout=5.0)

            engine._launcher.mark_interrupted_all()
            engine._draining = True

            time.sleep(0.1)
            engine._shutdown = True

            assert _wait_until(lambda: not t.is_alive(), timeout=5.0)
        finally:
            engine._shutdown = True
            t.join(timeout=5)
            for rt in list(engine._launcher._running.values()):
                try:
                    os.killpg(rt.proc.pid, 9)
                except Exception:
                    pass


class TestDrainNoNewLaunches:
    """AC-1: while draining, new tasks are not launched."""

    def test_no_new_launches_during_drain(self, tmp_path):
        jobs = tmp_path / "jobs"
        done = jobs / "done"
        engine = _make_engine(tmp_path, task_timeout=30.0)

        result_a = str(jobs / f"result-{UUID_A}.md")
        Path(result_a).parent.mkdir(parents=True, exist_ok=True)
        path = jobs / "exec.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"uuid": UUID_A, "command": f"sleep 30 && echo ok > {result_a}",
                        "depends": []}) + "\n",
            encoding="utf-8",
        )

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        try:
            assert _wait_until(lambda: engine._launcher.is_running(UUID_A), timeout=5.0)

            engine._launcher.mark_interrupted_all()
            engine._draining = True

            result_b = str(jobs / f"result-{UUID_B}.md")
            path.write_text(
                json.dumps({"uuid": UUID_A, "command": f"sleep 30 && echo ok > {result_a}",
                            "depends": []}) + "\n"
                + json.dumps({"uuid": UUID_B, "command": f"echo ok > {result_b}",
                              "depends": []}) + "\n",
                encoding="utf-8",
            )
            os.utime(path, (time.time() + 5, time.time() + 5))

            time.sleep(0.3)
            assert not is_done(str(done), UUID_B)

            engine._shutdown = True
            t.join(timeout=5)
        finally:
            engine._shutdown = True
            t.join(timeout=5)
            for rt in list(engine._launcher._running.values()):
                try:
                    os.killpg(rt.proc.pid, 9)
                except Exception:
                    pass
