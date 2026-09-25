"""AC-2 through AC-2f: interrupted task recording, rerun, and limits."""

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
from ghdag.dag.task_launcher import (
    _MAX_INTERRUPTED_RERUNS,
    TaskLauncher,
    _process_group_exists,
)


def _make_launcher(tmp_path: Path, task_timeout: float | None = None) -> TaskLauncher:
    jobs = tmp_path / "jobs"
    done = jobs / "done"
    done.mkdir(parents=True, exist_ok=True)
    (jobs / "running").mkdir(parents=True, exist_ok=True)
    config = DagConfig(
        exec_jsonl_path=jobs / "exec.jsonl",
        exec_done_dir=done,
        task_timeout=task_timeout,
        kill_grace=0.5,
        poll_interval=0.05,
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


def _write_running_file(jobs: Path, uuid: str, pid: int, pgid: int,
                        interrupted_at: str | None = None,
                        interrupted_reruns: int = 0) -> None:
    running_dir = jobs / "running"
    running_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "pid": pid,
        "pgid": pgid,
        "engine": "",
        "started_at": "2026-09-24T02:13:00+00:00",
        "has_resume": False,
    }
    if interrupted_at is not None:
        payload["interrupted_at"] = interrupted_at
    if interrupted_reruns:
        payload["interrupted_reruns"] = interrupted_reruns
    path = running_dir / f"{uuid}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")


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


UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
UUID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class TestMarkInterruptedAll:
    """AC-2a: mark_interrupted_all adds interrupted_at without touching existing fields."""

    def test_adds_interrupted_at_to_two_running_files(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"

        proc_a = subprocess.Popen(["sleep", "30"], start_new_session=True)
        proc_b = subprocess.Popen(["sleep", "30"], start_new_session=True)
        try:
            _write_running_file(jobs, UUID_A, proc_a.pid, proc_a.pid)
            _write_running_file(jobs, UUID_B, proc_b.pid, proc_b.pid)

            task_a = Task(uuid=UUID_A, command="sleep 30")
            task_b = Task(uuid=UUID_B, command="sleep 30")
            launcher._running[UUID_A] = MagicMock(uuid=UUID_A, task=task_a)
            launcher._running[UUID_B] = MagicMock(uuid=UUID_B, task=task_b)

            recorded = launcher.mark_interrupted_all()

            assert set(recorded) == {UUID_A, UUID_B}
            for uuid in (UUID_A, UUID_B):
                data = json.loads((jobs / "running" / f"{uuid}.json").read_text())
                assert "interrupted_at" in data
                assert data["pid"] in (proc_a.pid, proc_b.pid)
                assert data["engine"] == ""
                assert data["has_resume"] is False
                assert "started_at" in data
        finally:
            _force_kill_pgid(proc_a.pid)
            _force_kill_pgid(proc_b.pid)
            proc_a.wait(timeout=2)
            proc_b.wait(timeout=2)

    def test_missing_running_file_skipped_no_exception(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        task_a = Task(uuid=UUID_A, command="sleep 30")
        launcher._running[UUID_A] = MagicMock(uuid=UUID_A, task=task_a)

        recorded = launcher.mark_interrupted_all()

        assert recorded == []

    def test_one_missing_one_present(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"

        proc_b = subprocess.Popen(["sleep", "30"], start_new_session=True)
        try:
            _write_running_file(jobs, UUID_B, proc_b.pid, proc_b.pid)

            task_a = Task(uuid=UUID_A, command="sleep 30")
            task_b = Task(uuid=UUID_B, command="sleep 30")
            launcher._running[UUID_A] = MagicMock(uuid=UUID_A, task=task_a)
            launcher._running[UUID_B] = MagicMock(uuid=UUID_B, task=task_b)

            recorded = launcher.mark_interrupted_all()

            assert UUID_B in recorded
            assert UUID_A not in recorded
            data = json.loads((jobs / "running" / f"{UUID_B}.json").read_text())
            assert "interrupted_at" in data
        finally:
            _force_kill_pgid(proc_b.pid)
            proc_b.wait(timeout=2)


class TestAdoptOrphansInterrupted:
    """AC-2b, AC-2c, AC-2d: adopt_orphans behavior with interrupted_at."""

    def test_live_pgid_with_interrupted_at_is_killed_and_queued_for_rerun(self, tmp_path):
        """AC-2b: alive process with interrupted_at gets killed, queued for rerun."""
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"

        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        try:
            _write_running_file(
                jobs, UUID_A, proc.pid, pgid,
                interrupted_at="2026-09-24T02:13:00+00:00",
                interrupted_reruns=0,
            )
            task = Task(uuid=UUID_A, command="sleep 30")

            adopted = launcher.adopt_orphans({UUID_A: task})

            assert UUID_A not in adopted
            assert not launcher.is_running(UUID_A)
            assert UUID_A in launcher._pending_reruns
            assert launcher._pending_reruns[UUID_A] == 1

            proc.wait(timeout=3)
            assert not _process_group_exists(pgid)
        finally:
            _force_kill_pgid(pgid)
            if proc.poll() is None:
                proc.wait(timeout=2)

    def test_dead_pgid_with_interrupted_at_queued_for_rerun(self, tmp_path):
        """AC-2b (dead): dead process with interrupted_at queued for rerun."""
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"

        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        _force_kill_pgid(pgid)
        proc.wait(timeout=3)
        assert _wait_until(lambda: not _process_group_exists(pgid), timeout=3.0)

        _write_running_file(
            jobs, UUID_A, proc.pid, pgid,
            interrupted_at="2026-09-24T02:13:00+00:00",
            interrupted_reruns=0,
        )
        task = Task(uuid=UUID_A, command="sleep 30")

        adopted = launcher.adopt_orphans({UUID_A: task})

        assert UUID_A not in adopted
        assert not launcher.is_running(UUID_A)
        assert UUID_A in launcher._pending_reruns
        done_dir = jobs / "done"
        assert not is_done(str(done_dir), UUID_A)

    def test_interrupted_reruns_at_limit_becomes_orphan(self, tmp_path):
        """AC-2c: interrupted_reruns >= _MAX_INTERRUPTED_RERUNS falls back to orphan."""
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        done = jobs / "done"

        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        _force_kill_pgid(pgid)
        proc.wait(timeout=3)
        assert _wait_until(lambda: not _process_group_exists(pgid), timeout=3.0)

        _write_running_file(
            jobs, UUID_A, proc.pid, pgid,
            interrupted_at="2026-09-24T02:13:00+00:00",
            interrupted_reruns=_MAX_INTERRUPTED_RERUNS,
        )
        task = Task(uuid=UUID_A, command="sleep 30")

        adopted = launcher.adopt_orphans({UUID_A: task})

        assert UUID_A not in adopted
        assert UUID_A not in launcher._pending_reruns
        assert is_done(str(done), UUID_A)
        content = (done / UUID_A).read_text(encoding="utf-8").strip()
        assert content == DONE_ORPHANED_ON_RESTART
        launcher._hooks.on_task_failure.assert_called_once()

    def test_no_interrupted_at_follows_normal_orphan_path(self, tmp_path):
        """AC-2d: running file without interrupted_at uses existing orphan logic."""
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        done = jobs / "done"

        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        _force_kill_pgid(pgid)
        proc.wait(timeout=3)
        assert _wait_until(lambda: not _process_group_exists(pgid), timeout=3.0)

        _write_running_file(jobs, UUID_A, proc.pid, pgid)
        task = Task(uuid=UUID_A, command="sleep 30")

        adopted = launcher.adopt_orphans({UUID_A: task})

        assert UUID_A not in adopted
        assert UUID_A not in launcher._pending_reruns
        assert is_done(str(done), UUID_A)
        content = (done / UUID_A).read_text(encoding="utf-8").strip()
        assert content == DONE_ORPHANED_ON_RESTART


class TestRerunAfterInterrupt:
    """AC-2: after adopt, pending_reruns entry is used on launch."""

    def test_launch_with_pending_rerun_sets_env_and_reruns_counter(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        done = jobs / "done"

        env_file = tmp_path / "env_out.txt"
        result_path = str(jobs / f"result-{UUID_A}.md")
        Path(result_path).parent.mkdir(parents=True, exist_ok=True)

        launcher._pending_reruns[UUID_A] = 1

        task = Task(
            uuid=UUID_A,
            command=f"printenv GHDAG_PREVIOUS_ATTEMPT > {env_file} && echo ok > {result_path}",
            result_path=result_path,
        )
        launched = launcher.launch(UUID_A, task)
        assert launched

        assert UUID_A not in launcher._pending_reruns

        def _poll():
            launcher.check_completions()
            return UUID_A not in launcher._running

        assert _wait_until(_poll, timeout=10.0)

        env_content = env_file.read_text(encoding="utf-8").strip()
        assert env_content == "interrupted"

        assert is_done(str(done), UUID_A)
        assert (done / UUID_A).read_text(encoding="utf-8").strip() == "0"

    def test_launch_without_pending_rerun_no_env_var(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        done = jobs / "done"

        env_file = tmp_path / "env_out.txt"
        result_path = str(jobs / f"result-{UUID_A}.md")
        Path(result_path).parent.mkdir(parents=True, exist_ok=True)

        task = Task(
            uuid=UUID_A,
            command=(
                f'if [ -n "$GHDAG_PREVIOUS_ATTEMPT" ]; then '
                f'echo "HAS_ENV=$GHDAG_PREVIOUS_ATTEMPT" > {env_file}; fi '
                f'&& echo ok > {result_path}'
            ),
            result_path=result_path,
        )
        launched = launcher.launch(UUID_A, task)
        assert launched

        def _poll():
            launcher.check_completions()
            return UUID_A not in launcher._running

        assert _wait_until(_poll, timeout=10.0)

        assert not env_file.exists()
        assert is_done(str(done), UUID_A)


class TestInterruptedNoDone:
    """AC-2f: interrupted task does not get on_task_failure or done marker."""

    def test_interrupted_task_no_failure_hook_no_done(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        done = jobs / "done"

        result_path = str(jobs / f"result-{UUID_A}.md")
        Path(result_path).parent.mkdir(parents=True, exist_ok=True)

        task = Task(uuid=UUID_A, command=f"sleep 30 && echo ok > {result_path}",
                    result_path=result_path)
        launched = launcher.launch(UUID_A, task)
        assert launched

        assert _wait_until(lambda: launcher.is_running(UUID_A), timeout=3.0)

        launcher._interrupting.add(UUID_A)
        rt = launcher._running[UUID_A]
        from ghdag.dag.task_launcher import _signal_process_tree
        _signal_process_tree(rt.proc, signal.SIGTERM)
        rt.term_sent_at = time.monotonic()

        def _poll():
            launcher.check_completions()
            return not launcher.is_running(UUID_A)

        assert _wait_until(_poll, timeout=5.0)

        assert not is_done(str(done), UUID_A)
        launcher._hooks.on_task_failure.assert_not_called()

        running_file = jobs / "running" / f"{UUID_A}.json"
        assert running_file.exists()
