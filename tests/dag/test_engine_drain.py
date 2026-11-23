"""AC-1, AC-2e: DagEngine drain behavior on SIGTERM."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from ghdag_dag_conftest_helpers import MemoryHooks

from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig
from ghdag.io.done import is_done


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
    hooks = MemoryHooks()
    return DagEngine(config, hooks=hooks)


def _write_exec_jsonl(jobs: Path, uuid: str, command: str) -> None:
    path = jobs / "exec.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"uuid": uuid, "command": command, "depends": []}) + "\n",
        encoding="utf-8",
    )


def _wait_until(predicate, *, timeout: float = 10.0, interval: float = 0.05) -> bool:
    """Compatibility helper for legacy tests outside the T13 ledger."""
    tick = threading.Event()
    attempts = max(1, int(timeout / max(interval, 0.001)))
    for _ in range(attempts):
        if predicate():
            return True
        tick.wait(interval)
    return predicate()


UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
UUID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class TestDrainTimeout:
    """AC-2: drain deadline exceeded — done marker not written, running file has interrupted_at."""

    def test_drain_deadline_exceeded_leaves_interrupted_at(self, tmp_path, fake_clock):
        jobs = tmp_path / "jobs"
        # This test drives the drain deadline explicitly.  Disabling the
        # independent task timeout prevents the fast fake clock from racing
        # ahead and completing the task through the timeout path first.
        engine = _make_engine(tmp_path, task_timeout=None)
        result_path = str(jobs / f"result-{UUID_A}.md")
        Path(result_path).parent.mkdir(parents=True, exist_ok=True)
        _write_exec_jsonl(jobs, UUID_A, f"sleep 30 && echo ok > {result_path}")

        engine_stopped = threading.Event()

        def run_engine() -> None:
            try:
                engine.run()
            finally:
                engine_stopped.set()

        t = threading.Thread(target=run_engine, daemon=True)
        t.start()
        try:
            assert engine._hooks.wait_for(lambda: engine._launcher.is_running(UUID_A))

            engine._launcher.mark_interrupted_all()
            engine._draining = True
            engine._drain_deadline = fake_clock.monotonic() + 0.2
            fake_clock.advance(0.2)

            engine_stopped.wait()

            done_dir = jobs / "done"
            assert not is_done(str(done_dir), UUID_A)

            running_file = jobs / "running" / f"{UUID_A}.json"
            assert running_file.exists()
            data = json.loads(running_file.read_text(encoding="utf-8"))
            assert "interrupted_at" in data
        finally:
            engine._shutdown = True
            t.join()
            for rt in list(engine._launcher._running.values()):
                try:
                    os.killpg(rt.proc.pid, 9)
                except Exception:
                    pass
