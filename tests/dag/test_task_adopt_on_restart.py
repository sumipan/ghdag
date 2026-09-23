"""AC-2: TaskLauncher.adopt_orphans — real-process adopt and orphan handling."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.core.vocabulary import DONE_ORPHANED_ON_RESTART
from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
from ghdag.dag.models import DagConfig, Task
from ghdag.dag.state import is_done
from ghdag.dag.task_launcher import TaskLauncher, _process_group_exists


def _make_launcher(tmp_path: Path) -> TaskLauncher:
    jobs = tmp_path / "jobs"
    done = jobs / "done"
    done.mkdir(parents=True, exist_ok=True)
    (jobs / "running").mkdir(parents=True, exist_ok=True)
    config = DagConfig(
        exec_jsonl_path=jobs / "exec.jsonl",
        exec_done_dir=done,
        task_timeout=None,
        kill_grace=0.4,
        poll_interval=0.05,
    )
    hooks = MagicMock()
    return TaskLauncher(
        config,
        hooks=hooks,
        circuit_breaker=CircuitBreakerPolicy(float("inf"), 2**31),
        fanout_manager=MagicMock(),
        promote_fn=MagicMock(),
    )


def _write_running_file(jobs: Path, uuid: str, pid: int, pgid: int) -> None:
    running_dir = jobs / "running"
    running_dir.mkdir(parents=True, exist_ok=True)
    path = running_dir / f"{uuid}.json"
    path.write_text(
        json.dumps({"pid": pid, "pgid": pgid, "engine": "", "started_at": "2026-09-14T20:10:00+00:00"}),
        encoding="utf-8",
    )


def _force_kill_pgid(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _read_done(done_dir: Path, uuid: str) -> str:
    return (done_dir / uuid).read_text(encoding="utf-8").strip()


UUID_LIVE = "11111111-1111-1111-1111-111111111111"
UUID_DEAD = "22222222-2222-2222-2222-222222222222"
UUID_UNK = "33333333-3333-3333-3333-333333333333"
UUID_CORRUPT = "44444444-4444-4444-4444-444444444444"


class TestAdoptLiveProcess:
    def test_adopt_live_process_is_running(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        result_path = str(jobs / f"result-{UUID_LIVE}.md")
        task = Task(uuid=UUID_LIVE, command="sleep 30", result_path=result_path)

        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        try:
            _write_running_file(jobs, UUID_LIVE, proc.pid, pgid)

            adopted = launcher.adopt_orphans({UUID_LIVE: task})

            assert adopted == [UUID_LIVE]
            assert launcher.is_running(UUID_LIVE)
            assert launcher.running_count == 1
            assert UUID_LIVE not in launcher._running
            assert UUID_LIVE in launcher._adopted
        finally:
            _force_kill_pgid(pgid)
            proc.wait(timeout=3)

    def test_adopt_live_then_kill_closes_as_orphan(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        done = jobs / "done"
        result_path = str(jobs / f"result-{UUID_LIVE}.md")
        task = Task(uuid=UUID_LIVE, command="sleep 30", result_path=result_path)

        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        try:
            _write_running_file(jobs, UUID_LIVE, proc.pid, pgid)
            launcher.adopt_orphans({UUID_LIVE: task})
            assert launcher.is_running(UUID_LIVE)

            launcher.check_completions()
            assert is_done(done, UUID_LIVE) is False

            _force_kill_pgid(pgid)
            proc.wait(timeout=3)
            assert _wait_until(lambda: not _process_group_exists(pgid), timeout=3.0)

            launcher.check_completions()

            assert not launcher.is_running(UUID_LIVE)
            assert is_done(done, UUID_LIVE)
            assert _read_done(done, UUID_LIVE) == DONE_ORPHANED_ON_RESTART
            result_text = Path(result_path).read_text(encoding="utf-8")
            assert result_text.startswith("ORPHANED_ON_RESTART:")
            launcher._hooks.on_task_failure.assert_called_once()
            assert launcher._hooks.on_task_failure.call_args[0][3] == "orphaned_on_restart"
        finally:
            _force_kill_pgid(pgid)

    def test_check_completions_idempotent_after_orphan_close(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        task = Task(uuid=UUID_LIVE, command="sleep 30")

        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        try:
            _write_running_file(jobs, UUID_LIVE, proc.pid, pgid)
            launcher.adopt_orphans({UUID_LIVE: task})

            _force_kill_pgid(pgid)
            proc.wait(timeout=3)
            assert _wait_until(lambda: not _process_group_exists(pgid), timeout=3.0)

            launcher.check_completions()
            assert launcher._hooks.on_task_failure.call_count == 1

            launcher.check_completions()
            assert launcher._hooks.on_task_failure.call_count == 1
        finally:
            _force_kill_pgid(pgid)


class TestAdoptDeadProcess:
    def test_already_dead_process_immediately_orphaned(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        done = jobs / "done"
        task = Task(uuid=UUID_DEAD, command="sleep 30")

        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        _force_kill_pgid(pgid)
        proc.wait(timeout=3)
        assert _wait_until(lambda: not _process_group_exists(pgid), timeout=3.0)

        _write_running_file(jobs, UUID_DEAD, proc.pid, pgid)

        adopted = launcher.adopt_orphans({UUID_DEAD: task})

        assert adopted == []
        assert not launcher.is_running(UUID_DEAD)
        assert is_done(done, UUID_DEAD)
        assert _read_done(done, UUID_DEAD) == DONE_ORPHANED_ON_RESTART
        launcher._hooks.on_task_failure.assert_called_once()

    def test_corrupt_json_treated_as_dead(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        done = jobs / "done"
        task = Task(uuid=UUID_CORRUPT, command="sleep 30")

        running_dir = jobs / "running"
        running_dir.mkdir(parents=True, exist_ok=True)
        (running_dir / f"{UUID_CORRUPT}.json").write_text("{{NOT JSON}}", encoding="utf-8")

        adopted = launcher.adopt_orphans({UUID_CORRUPT: task})

        assert adopted == []
        assert not launcher.is_running(UUID_CORRUPT)
        assert is_done(done, UUID_CORRUPT)
        assert _read_done(done, UUID_CORRUPT) == DONE_ORPHANED_ON_RESTART

    def test_pgid_not_int_treated_as_dead(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        done = jobs / "done"
        task = Task(uuid=UUID_CORRUPT, command="sleep 30")

        running_dir = jobs / "running"
        running_dir.mkdir(parents=True, exist_ok=True)
        (running_dir / f"{UUID_CORRUPT}.json").write_text(
            json.dumps({"pid": 12345, "pgid": "not-an-int"}), encoding="utf-8"
        )

        adopted = launcher.adopt_orphans({UUID_CORRUPT: task})

        assert adopted == []
        assert is_done(done, UUID_CORRUPT)
        assert _read_done(done, UUID_CORRUPT) == DONE_ORPHANED_ON_RESTART


class TestAdoptUnknownUUID:
    def test_unknown_uuid_running_file_ignored(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        done = jobs / "done"

        running_dir = jobs / "running"
        running_dir.mkdir(parents=True, exist_ok=True)
        unk_path = running_dir / f"{UUID_UNK}.json"
        unk_path.write_text(json.dumps({"pid": 1, "pgid": 1}), encoding="utf-8")

        adopted = launcher.adopt_orphans({})

        assert adopted == []
        assert not is_done(done, UUID_UNK)
        assert unk_path.exists()


class TestAdoptNoRunningDir:
    def test_missing_running_dir_returns_empty(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        (jobs / "running").rmdir()

        adopted = launcher.adopt_orphans({UUID_LIVE: Task(uuid=UUID_LIVE, command="true")})

        assert adopted == []
        assert launcher.running_count == 0
