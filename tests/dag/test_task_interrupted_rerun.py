"""AC-2 through AC-2f: interrupted task recording, rerun, and limits."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.core.vocabulary import DONE_ORPHANED_ON_RESTART
from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
from ghdag.dag.models import DagConfig, Task
from ghdag.dag.task_launcher import (
    _MAX_INTERRUPTED_RERUNS,
    TaskLauncher,
    _process_group_exists,
)
from ghdag.io.done import is_done


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


def _spin_until(predicate) -> bool:
    while not predicate():
        pass
    return True


class _FakeClock:
    def __init__(self, mono: float = 10_000.0, wall: float = 1_700_000_000.0) -> None:
        self._mono = mono
        self._wall = wall

    def monotonic(self) -> float:
        return self._mono

    def time(self) -> float:
        return self._wall

    def advance(self, sec: float) -> None:
        self._mono += sec
        self._wall += sec


UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
UUID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class TestMarkInterruptedAll:
    """AC-2a: mark_interrupted_all adds interrupted_at without touching existing fields."""


    def test_missing_running_file_skipped_no_exception(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        task_a = Task(uuid=UUID_A, command="sleep 30")
        launcher._running[UUID_A] = MagicMock(uuid=UUID_A, task=task_a)

        recorded = launcher.mark_interrupted_all()

        assert recorded == []



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

            proc.wait()
            assert not _process_group_exists(pgid)
        finally:
            _force_kill_pgid(pgid)
            if proc.poll() is None:
                proc.wait()


    def test_interrupted_reruns_at_limit_becomes_orphan(self, tmp_path):
        """AC-2c: interrupted_reruns >= _MAX_INTERRUPTED_RERUNS falls back to orphan."""
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        done = jobs / "done"

        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        _force_kill_pgid(pgid)
        proc.wait()
        assert _spin_until(lambda: not _process_group_exists(pgid))

        _write_running_file(
            jobs, UUID_A, proc.pid, pgid,
            interrupted_at="2026-09-24T02:13:00+00:00",
            interrupted_reruns=_MAX_INTERRUPTED_RERUNS,
        )
        result_path = tmp_path / "result.md"
        task = Task(uuid=UUID_A, command="sleep 30", result_path=str(result_path))

        adopted = launcher.adopt_orphans({UUID_A: task})

        assert UUID_A not in adopted
        assert UUID_A not in launcher._pending_reruns
        assert is_done(str(done), UUID_A)
        content = (done / UUID_A).read_text(encoding="utf-8").strip()
        assert content == DONE_ORPHANED_ON_RESTART
        assert result_path.read_text(encoding="utf-8") == (
            "ORPHANED_ON_RESTART: interrupted rerun limit reached"
        )
        launcher._hooks.on_task_failure.assert_called_once()



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

        assert _spin_until(_poll)

        env_content = env_file.read_text(encoding="utf-8").strip()
        assert env_content == "interrupted"

        assert is_done(str(done), UUID_A)
        assert (done / UUID_A).read_text(encoding="utf-8").strip() == "0"



