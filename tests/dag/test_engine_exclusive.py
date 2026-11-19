"""Engine tests for exclusive tasks (issue #7363)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import ANY, MagicMock

from ghdag.core.vocabulary import DONE_CANCELLED, DONE_ORPHANED_ON_RESTART
from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig, Task
from ghdag.dag.task_launcher import TaskLauncher, _process_group_exists
from ghdag.io.done import interpret_done, is_done, load_succeeded_from_dir, read_done_content
from ghdag.status import EXCLUSIVE_STATE_FILE, read_exclusive_gate

UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
UUID_X = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
UUID_B = "cccccccc-cccc-cccc-cccc-cccccccccccc"
UUID_C = "dddddddd-dddd-dddd-dddd-dddddddddddd"
UUID_X1 = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
UUID_X2 = "ffffffff-ffff-ffff-ffff-ffffffffffff"
UUID_A2 = "11111111-2222-3333-4444-555555555555"


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def _make_config(tmp_path: Path, records: list[dict], **overrides) -> DagConfig:
    exec_jsonl = tmp_path / "jobs" / "exec.jsonl"
    exec_jsonl.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(exec_jsonl, records)
    defaults = dict(
        exec_jsonl_path=str(exec_jsonl),
        exec_done_dir=str(tmp_path / "jobs" / "done"),
        poll_interval=0.05,
        launch_stagger=0.0,
        lock_file=str(tmp_path / "lock"),
    )
    defaults.update(overrides)
    Path(defaults["exec_done_dir"]).mkdir(parents=True, exist_ok=True)
    return DagConfig(**defaults)


def _plain_task(uuid: str, command: str, **extra) -> dict:
    return {"uuid": uuid, "command": command, "depends": [], **extra}


def _exclusive_task(uuid: str, command: str, **extra) -> dict:
    ann = {"_exclusive": "true", **extra.pop("annotations", {})}
    return {"uuid": uuid, "command": command, "depends": [], "annotations": ann, **extra}


def _wait_until(predicate, *, timeout: float = 15.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _stamp_cmd(stamp_dir: Path, label: str, body: str) -> str:
    start = stamp_dir / f"{label}.start"
    return (
        f"bash -o pipefail -c 'date +%s.%N > {start}; {body}'"
    )


def _read_stamp(stamp_dir: Path, label: str, suffix: str = "start") -> float:
    return float((stamp_dir / f"{label}.{suffix}").read_text(encoding="utf-8").strip())


class TestExclusiveLaunchOrdering:
    """Exclusive gate defers launches until the queue is idle, then blocks others."""

    def test_exclusive_waits_for_running_then_blocks_later(self, tmp_path):
        stamp_dir = tmp_path / "stamps"
        stamp_dir.mkdir()
        records = [
            _plain_task(UUID_A, _stamp_cmd(stamp_dir, "A", "sleep 1.5")),
        ]
        config = _make_config(tmp_path, records, max_concurrency=None)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)
        exec_path = Path(config.exec_jsonl_path)

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        try:
            assert _wait_until(lambda: engine._launcher.is_running(UUID_A), timeout=5.0)
            _append_jsonl(
                exec_path,
                [
                    _exclusive_task(
                        UUID_X,
                        _stamp_cmd(stamp_dir, "X", "sleep 0.4"),
                    ),
                    _plain_task(
                        UUID_B,
                        _stamp_cmd(stamp_dir, "B", "sleep 0.2"),
                    ),
                ],
            )
            assert _wait_until(
                lambda: all(
                    is_done(config.exec_done_dir, uid)
                    for uid in (UUID_A, UUID_X, UUID_B)
                ),
                timeout=15.0,
            )
            ta, tx, tb = (
                _read_stamp(stamp_dir, "A"),
                _read_stamp(stamp_dir, "X"),
                _read_stamp(stamp_dir, "B"),
            )
            assert ta < tx < tb
            assert tx > _read_stamp(stamp_dir, "A")  # X after A started
        finally:
            engine._shutdown = True
            t.join(timeout=5.0)

    def test_downstream_of_running_task_waits_for_exclusive(self, tmp_path):
        stamp_dir = tmp_path / "stamps"
        stamp_dir.mkdir()
        records = [
            _plain_task(UUID_A, _stamp_cmd(stamp_dir, "A", "sleep 0.8")),
            _exclusive_task(UUID_X, _stamp_cmd(stamp_dir, "X", "sleep 0.4")),
            _plain_task(
                UUID_A2,
                _stamp_cmd(stamp_dir, "A2", "sleep 0.1"),
                depends=[UUID_A],
            ),
        ]
        config = _make_config(tmp_path, records, max_concurrency=None)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)
        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        try:
            assert _wait_until(
                lambda: all(
                    is_done(config.exec_done_dir, uid)
                    for uid in (UUID_A, UUID_X, UUID_A2)
                ),
                timeout=15.0,
            )
            ta2 = _read_stamp(stamp_dir, "A2")
            tx_end = float((stamp_dir / "X.start").read_text().strip()) + 0.35
            assert ta2 > tx_end
        finally:
            engine._shutdown = True
            t.join(timeout=5.0)

    def test_only_exclusive_runs_while_it_runs(self, tmp_path):
        records = [
            _exclusive_task(UUID_X, "sleep 1.2"),
            _plain_task(UUID_B, "sleep 0.2"),
            _plain_task(UUID_C, "sleep 0.2"),
        ]
        config = _make_config(tmp_path, records, max_concurrency=None)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)
        running_dir = Path(config.exec_done_dir).parent / "running"

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        try:
            assert _wait_until(lambda: engine._launcher.is_running(UUID_X), timeout=5.0)
            names = {p.stem for p in running_dir.glob("*.json")}
            assert names == {UUID_X}
        finally:
            engine._shutdown = True
            t.join(timeout=8.0)

    def test_failed_exclusive_releases_others(self, tmp_path):
        records = [
            _exclusive_task(UUID_X, "exit 1"),
            _plain_task(UUID_B, "exit 0"),
        ]
        config = _make_config(tmp_path, records)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)
        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        try:
            assert _wait_until(
                lambda: is_done(config.exec_done_dir, UUID_X)
                and is_done(config.exec_done_dir, UUID_B),
                timeout=15.0,
            )
            assert interpret_done(read_done_content(config.exec_done_dir, UUID_X)) != "success"
            assert UUID_B in load_succeeded_from_dir(config.exec_done_dir)
        finally:
            engine._shutdown = True
            t.join(timeout=5.0)

    def test_two_exclusive_tasks_run_one_at_a_time_in_order(self, tmp_path):
        stamp_dir = tmp_path / "stamps"
        stamp_dir.mkdir()
        records = [
            _exclusive_task(UUID_X1, _stamp_cmd(stamp_dir, "X1", "sleep 0.35")),
            _exclusive_task(UUID_X2, _stamp_cmd(stamp_dir, "X2", "sleep 0.35")),
            _plain_task(UUID_C, _stamp_cmd(stamp_dir, "C", "sleep 0.1")),
        ]
        config = _make_config(tmp_path, records)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)
        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        try:
            assert _wait_until(
                lambda: all(
                    is_done(config.exec_done_dir, uid)
                    for uid in (UUID_X1, UUID_X2, UUID_C)
                ),
                timeout=15.0,
            )
            tx1, tx2, tc = (
                _read_stamp(stamp_dir, "X1"),
                _read_stamp(stamp_dir, "X2"),
                _read_stamp(stamp_dir, "C"),
            )
            assert tx1 < tx2 < tc
            assert tx2 - tx1 > 0.25
        finally:
            engine._shutdown = True
            t.join(timeout=8.0)


class TestExclusiveStateFile:
    def test_exclusive_state_file_tracks_gate(self, tmp_path):
        records = [
            _plain_task(UUID_A, "sleep 0.5"),
            _exclusive_task(UUID_X, "sleep 0.4"),
        ]
        config = _make_config(tmp_path, records)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)
        gate_path = Path(config.exec_done_dir).parent / EXCLUSIVE_STATE_FILE

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        try:
            assert _wait_until(lambda: gate_path.is_file(), timeout=8.0)
            waiting = json.loads(gate_path.read_text(encoding="utf-8"))
            assert waiting == {"uuid": UUID_X, "state": "waiting"}

            assert _wait_until(
                lambda: read_exclusive_gate(Path(config.exec_done_dir).parent)
                == (UUID_X, "running"),
                timeout=8.0,
            )

            assert _wait_until(lambda: is_done(config.exec_done_dir, UUID_X), timeout=8.0)
            assert _wait_until(lambda: not gate_path.exists(), timeout=5.0)
        finally:
            engine._shutdown = True
            t.join(timeout=8.0)


class TestExclusiveDrainAndAdopt:
    def test_drain_leaves_running_exclusive_alive(self, tmp_path):
        jobs = tmp_path / "jobs"
        jobs.mkdir(parents=True)
        _write_jsonl(
            jobs / "exec.jsonl",
            [_exclusive_task(UUID_X, "sleep 30")],
        )
        config = DagConfig(
            exec_jsonl_path=jobs / "exec.jsonl",
            exec_done_dir=jobs / "done",
            poll_interval=0.05,
            launch_stagger=0.0,
            lock_file=tmp_path / "lock",
            task_timeout=30.0,
            kill_grace=0.5,
        )
        (jobs / "done").mkdir(parents=True)
        engine = DagEngine(config, MagicMock())
        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        try:
            assert _wait_until(lambda: engine._launcher.is_running(UUID_X), timeout=5.0)
            engine._launcher.mark_interrupted_all()
            engine._draining = True
            engine._drain_deadline = time.monotonic() + 30.0
            assert _wait_until(lambda: not t.is_alive(), timeout=2.0)
            running_file = jobs / "running" / f"{UUID_X}.json"
            assert running_file.exists()
            data = json.loads(running_file.read_text(encoding="utf-8"))
            assert "interrupted_at" in data
            assert not is_done(str(jobs / "done"), UUID_X)
            pgid = data["pgid"]
            assert _process_group_exists(pgid)
        finally:
            engine._shutdown = True
            t.join(timeout=3.0)
            data = json.loads((jobs / "running" / f"{UUID_X}.json").read_text())
            try:
                os.killpg(data["pgid"], signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def test_interrupted_live_exclusive_is_adopted_not_rerun(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        try:
            payload = {
                "pid": proc.pid,
                "pgid": pgid,
                "engine": "",
                "started_at": "2026-09-24T02:13:00+00:00",
                "interrupted_at": "2026-09-24T02:13:01+00:00",
            }
            (jobs / "running" / f"{UUID_X}.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            task = Task(
                uuid=UUID_X,
                command="sleep 30",
                annotations={"_exclusive": "true"},
            )
            adopted = launcher.adopt_orphans({UUID_X: task})
            assert UUID_X in adopted
            assert UUID_X not in launcher._pending_reruns
            assert _process_group_exists(pgid)
        finally:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait()

    def test_interrupted_dead_exclusive_is_orphaned_not_rerun(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        os.killpg(pgid, signal.SIGKILL)
        proc.wait()
        assert _wait_until(lambda: not _process_group_exists(pgid), timeout=2.0)
        payload = {
            "pid": proc.pid,
            "pgid": pgid,
            "engine": "",
            "started_at": "2026-09-24T02:13:00+00:00",
            "interrupted_at": "2026-09-24T02:13:01+00:00",
        }
        (jobs / "running" / f"{UUID_X}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        task = Task(uuid=UUID_X, command="sleep 30", annotations={"_exclusive": "true"})
        launcher.adopt_orphans({UUID_X: task})
        assert UUID_X not in launcher._pending_reruns
        content = read_done_content(jobs / "done", UUID_X)
        assert content is not None and DONE_ORPHANED_ON_RESTART in content


def _make_launcher(tmp_path: Path) -> TaskLauncher:
    jobs = tmp_path / "jobs"
    done = jobs / "done"
    done.mkdir(parents=True, exist_ok=True)
    (jobs / "running").mkdir(parents=True, exist_ok=True)
    config = DagConfig(
        exec_jsonl_path=jobs / "exec.jsonl",
        exec_done_dir=done,
        poll_interval=0.05,
        kill_grace=0.5,
    )
    hooks = MagicMock()
    hooks.check_rejected.return_value = None
    hooks.check_pipeline_status.return_value = None
    return TaskLauncher(
        config,
        hooks=hooks,
        circuit_breaker=CircuitBreakerPolicy(float("inf"), 2**31),
        fanout_manager=MagicMock(),
        promote_fn=MagicMock(),
    )


class TestCancelWaitingExclusive:
    def test_cancel_waiting_exclusive_marks_cancelled(self, tmp_path):
        stamp_dir = tmp_path / "stamps"
        stamp_dir.mkdir()
        records = [
            _plain_task(UUID_A, _stamp_cmd(stamp_dir, "A", "sleep 0.5")),
            _exclusive_task(UUID_X, _stamp_cmd(stamp_dir, "X", "sleep 0.5")),
            _plain_task(UUID_B, _stamp_cmd(stamp_dir, "B", "sleep 0.2")),
        ]
        config = _make_config(tmp_path, records)
        hooks = MagicMock()
        hooks.check_rejected.return_value = False
        engine = DagEngine(config, hooks)
        queue_dir = Path(config.exec_done_dir).parent
        cancel_path = queue_dir / "cancel" / UUID_X

        t = threading.Thread(target=engine.run, daemon=True)
        t.start()
        try:
            assert _wait_until(
                lambda: read_exclusive_gate(queue_dir) == (UUID_X, "waiting"),
                timeout=8.0,
            )
            cancel_path.parent.mkdir(parents=True, exist_ok=True)
            cancel_path.write_text("", encoding="utf-8")
            assert _wait_until(lambda: is_done(config.exec_done_dir, UUID_X), timeout=8.0)
            assert not cancel_path.exists()
            hooks.on_task_cancelled.assert_called_with(UUID_X, ANY)
            assert read_done_content(config.exec_done_dir, UUID_X) == DONE_CANCELLED
            assert _wait_until(lambda: is_done(config.exec_done_dir, UUID_B), timeout=8.0)
            assert UUID_B in load_succeeded_from_dir(config.exec_done_dir)
        finally:
            engine._shutdown = True
            t.join(timeout=8.0)
