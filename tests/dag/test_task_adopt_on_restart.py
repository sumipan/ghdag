"""AC-2: TaskLauncher.adopt_orphans — real-process adopt and orphan handling."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

from ghdag.core.vocabulary import DONE_ORPHANED_ON_RESTART
from ghdag.dag._process_host import LocalProcessHost
from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
from ghdag.dag.engine import DagEngine
from ghdag.dag.models import DagConfig, Task
from ghdag.dag.task_launcher import (
    _MAX_INTERRUPTED_RERUNS,
    TaskLauncher,
)
from ghdag.io.done import is_done


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
    hooks.check_rejected.return_value = None
    hooks.check_pipeline_status.return_value = None
    return TaskLauncher(
        config,
        hooks=hooks,
        circuit_breaker=CircuitBreakerPolicy(float("inf"), 2**31),
        fanout_manager=MagicMock(),
        promote_fn=MagicMock(),
    )


def _write_running_file(
    jobs: Path,
    uuid: str,
    pid: int,
    pgid: int,
    *,
    interrupted_reruns: int | None = None,
) -> None:
    running_dir = jobs / "running"
    running_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "pid": pid,
        "pgid": pgid,
        "engine": "",
        "started_at": "2026-09-14T20:10:00+00:00",
    }
    if interrupted_reruns is not None:
        payload["interrupted_reruns"] = interrupted_reruns
    path = running_dir / f"{uuid}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")


def _force_kill_pgid(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _read_done(done_dir: Path, uuid: str) -> str:
    return (done_dir / uuid).read_text(encoding="utf-8").strip()


UUID_LIVE = "11111111-1111-1111-1111-111111111111"
UUID_DEAD = "22222222-2222-2222-2222-222222222222"
UUID_UNK = "33333333-3333-3333-3333-333333333333"
UUID_CORRUPT = "44444444-4444-4444-4444-444444444444"
UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


class TestUnmarkedDeadRerun:
    def test_unmarked_dead_task_is_queued_for_rerun(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        done = jobs / "done"

        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        _force_kill_pgid(pgid)
        proc.wait()
        assert not LocalProcessHost().group_exists(pgid)

        _write_running_file(jobs, UUID_A, proc.pid, pgid)

        env_file = tmp_path / "env_out.txt"
        result_path = str(jobs / f"result-{UUID_A}.md")
        task = Task(
            uuid=UUID_A,
            command=f"printenv GHDAG_PREVIOUS_ATTEMPT > {env_file} && echo ok > {result_path}",
            result_path=result_path,
        )

        adopted = launcher.adopt_orphans({UUID_A: task})

        assert adopted == []
        assert not is_done(done, UUID_A)
        assert not (jobs / "running" / f"{UUID_A}.json").exists()
        launcher._hooks.on_task_failure.assert_not_called()
        assert UUID_A in launcher._pending_reruns
        assert launcher._pending_reruns[UUID_A] == 1

        launched = launcher.launch(UUID_A, task)
        assert launched
        assert UUID_A not in launcher._pending_reruns

        running_path = jobs / "running" / f"{UUID_A}.json"
        assert running_path.exists()
        running_data = json.loads(running_path.read_text(encoding="utf-8"))
        assert running_data.get("interrupted_reruns") == 1

        launcher._running[UUID_A].proc.wait()
        launcher.check_completions()
        assert UUID_A not in launcher._running

        assert env_file.read_text(encoding="utf-8").strip() == "interrupted"

        launcher.terminate_all()
        launcher.check_completions()

    def test_unmarked_dead_task_at_rerun_limit_is_orphaned(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        done = jobs / "done"

        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        _force_kill_pgid(pgid)
        proc.wait()
        assert not LocalProcessHost().group_exists(pgid)

        _write_running_file(
            jobs, UUID_A, proc.pid, pgid, interrupted_reruns=_MAX_INTERRUPTED_RERUNS,
        )
        task = Task(uuid=UUID_A, command="sleep 30")

        launcher.adopt_orphans({UUID_A: task})

        assert UUID_A not in launcher._pending_reruns
        assert is_done(done, UUID_A)
        assert _read_done(done, UUID_A) == DONE_ORPHANED_ON_RESTART


class TestAdoptedTaskExitRerun:
    def test_adopted_task_exit_below_limit_is_queued_for_rerun(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        done = jobs / "done"
        task = Task(uuid=UUID_A, command="sleep 30")

        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        try:
            _write_running_file(jobs, UUID_A, proc.pid, pgid)
            adopted = launcher.adopt_orphans({UUID_A: task})
            assert UUID_A in adopted
            assert launcher.is_running(UUID_A)

            _force_kill_pgid(pgid)
            proc.wait()
            assert not LocalProcessHost().group_exists(pgid)

            launcher.check_completions()

            assert not launcher.is_running(UUID_A)
            assert not is_done(done, UUID_A)
            assert launcher._pending_reruns[UUID_A] == 1
            assert not (jobs / "running" / f"{UUID_A}.json").exists()
            launcher._hooks.on_task_failure.assert_not_called()
        finally:
            _force_kill_pgid(pgid)

    def test_adopted_task_exit_at_limit_is_orphaned(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        done = jobs / "done"
        result_path = str(jobs / "result.md")
        task = Task(uuid=UUID_A, command="sleep 30", result_path=result_path)

        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        try:
            _write_running_file(
                jobs, UUID_A, proc.pid, pgid, interrupted_reruns=_MAX_INTERRUPTED_RERUNS,
            )
            launcher.adopt_orphans({UUID_A: task})

            _force_kill_pgid(pgid)
            proc.wait()
            assert not LocalProcessHost().group_exists(pgid)

            launcher.check_completions()

            assert not launcher.is_running(UUID_A)
            assert is_done(done, UUID_A)
            assert _read_done(done, UUID_A) == DONE_ORPHANED_ON_RESTART
            launcher._hooks.on_task_failure.assert_called_once()
            assert launcher._hooks.on_task_failure.call_args[0][3] == "orphaned_on_restart"
        finally:
            _force_kill_pgid(pgid)


class TestExclusiveUnmarkedOrphan:
    def test_unmarked_dead_exclusive_task_is_orphaned(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        done = jobs / "done"

        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        _force_kill_pgid(pgid)
        proc.wait()
        assert not LocalProcessHost().group_exists(pgid)

        _write_running_file(jobs, UUID_A, proc.pid, pgid)
        task = Task(
            uuid=UUID_A,
            command="sleep 30",
            annotations={"_exclusive": "true"},
        )

        launcher.adopt_orphans({UUID_A: task})

        assert UUID_A not in launcher._pending_reruns
        assert is_done(done, UUID_A)
        assert _read_done(done, UUID_A) == DONE_ORPHANED_ON_RESTART

    def test_adopted_exclusive_task_exit_is_orphaned(self, tmp_path):
        launcher = _make_launcher(tmp_path)
        jobs = tmp_path / "jobs"
        done = jobs / "done"
        task = Task(
            uuid=UUID_A,
            command="sleep 30",
            annotations={"_exclusive": "true"},
        )

        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        try:
            _write_running_file(jobs, UUID_A, proc.pid, pgid)
            launcher.adopt_orphans({UUID_A: task})

            _force_kill_pgid(pgid)
            proc.wait()
            assert not LocalProcessHost().group_exists(pgid)

            launcher.check_completions()

            assert UUID_A not in launcher._pending_reruns
            assert is_done(done, UUID_A)
            assert _read_done(done, UUID_A) == DONE_ORPHANED_ON_RESTART
        finally:
            _force_kill_pgid(pgid)


class TestAdoptDeadProcess:
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


class TestEngineAdoptOnce:
    def test_engine_adopts_once_and_reruns_once_after_exit(self, tmp_path):
        jobs = tmp_path / "jobs"
        done = jobs / "done"
        done.mkdir(parents=True)
        exec_jsonl = jobs / "exec.jsonl"
        exec_jsonl.write_text(
            json.dumps({"uuid": UUID_LIVE, "command": "sleep 30", "depends": []}) + "\n",
            encoding="utf-8",
        )
        config = DagConfig(
            exec_jsonl_path=str(exec_jsonl),
            exec_done_dir=str(done),
            poll_interval=0.05,
            launch_stagger=0.0,
            lock_file=str(tmp_path / "lock"),
        )
        hooks = MagicMock()
        hooks.check_rejected.return_value = None
        hooks.check_pipeline_status.return_value = None
        engine = DagEngine(config, hooks=hooks)

        adopt_event = threading.Event()
        check_proceed = threading.Event()
        dead_done = threading.Event()
        live_relaunched = threading.Event()

        _restore_waiting = engine._launcher.restore_waiting
        _launch = engine._launcher.launch

        def _restore_waiting_sync(tasks, known_done):
            restored = _restore_waiting(tasks, known_done)
            adopt_event.set()
            check_proceed.wait()
            return restored

        def _on_task_success_side_effect(uuid, *args, **kwargs):
            if uuid == UUID_DEAD:
                dead_done.set()

        def _launch_and_notify(uuid, task):
            launched = _launch(uuid, task)
            if uuid == UUID_LIVE and launched:
                live_relaunched.set()
            return launched

        hooks.on_task_success.side_effect = _on_task_success_side_effect

        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = proc.pid
        t = threading.Thread(target=engine.run, daemon=True)
        try:
            _write_running_file(jobs, UUID_LIVE, proc.pid, pgid)

            with patch.object(
                engine._launcher, "adopt_orphans", wraps=engine._launcher.adopt_orphans
            ) as spy, patch.object(
                engine._launcher, "launch", side_effect=_launch_and_notify
            ) as launch_spy, patch.object(
                engine._launcher,
                "restore_waiting",
                side_effect=_restore_waiting_sync,
            ):
                t.start()
                adopt_event.wait()
                assert adopt_event.is_set()
                assert engine._launcher.is_running(UUID_LIVE)
                assert spy.call_count == 1

                exec_jsonl.write_text(
                    exec_jsonl.read_text(encoding="utf-8")
                    + json.dumps({"uuid": UUID_DEAD, "command": "true", "depends": []})
                    + "\n",
                    encoding="utf-8",
                )
                st = os.stat(exec_jsonl)
                os.utime(exec_jsonl, (st.st_atime + 5, st.st_mtime + 5))
                check_proceed.set()

                dead_done.wait()
                assert dead_done.is_set()
                assert spy.call_count == 1
                assert all(c.args[0] != UUID_LIVE for c in launch_spy.call_args_list)
                assert not is_done(str(done), UUID_LIVE)

                _force_kill_pgid(pgid)
                proc.wait()

                live_relaunched.wait()
                assert sum(
                    1 for c in launch_spy.call_args_list if c.args[0] == UUID_LIVE
                ) == 1
                assert not is_done(str(done), UUID_LIVE)
        finally:
            engine._shutdown = True
            t.join()
            engine._launcher.terminate_all()
            _force_kill_pgid(pgid)
            if proc.poll() is None:
                proc.wait()
