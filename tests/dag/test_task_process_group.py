"""Process-group isolation for DAG timeout / cancel (nexus #3257)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ghdag.core.vocabulary import DONE_CANCELLED, DONE_TIMEOUT
from ghdag.dag.circuit_breaker import CircuitBreakerPolicy
from ghdag.dag.models import DagConfig, Task
from ghdag.dag.task_launcher import (
    TaskLauncher,
    _process_group_exists,
    _signal_process_tree,
)
from ghdag.io.done import is_done

# 3-level tree: bash → python (dies on SIGTERM) → grandchild (ignores SIGTERM).
# Grandchild writes "<pid> <pgid>" to ready_path when alive.
_ORPHAN_TREE_PY = """\
import os
import signal
import sys
import time

ready = sys.argv[1]
pid = os.fork()
if pid == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    with open(ready, "w", encoding="utf-8") as f:
        f.write(f"{os.getpid()} {os.getpgrp()}\\n")
    while True:
        time.sleep(0.05)
else:
    os.waitpid(pid, 0)
"""


def _make_launcher(
    tmp_path: Path,
    *,
    task_timeout: float | None = 0.3,
    kill_grace: float = 0.4,
) -> TaskLauncher:
    jobs = tmp_path / "jobs"
    done = jobs / "done"
    done.mkdir(parents=True, exist_ok=True)
    (jobs / "running").mkdir(parents=True, exist_ok=True)
    (jobs / "cancel").mkdir(parents=True, exist_ok=True)
    config = DagConfig(
        exec_jsonl_path=jobs / "exec.jsonl",
        exec_done_dir=done,
        task_timeout=task_timeout,
        kill_grace=kill_grace,
        poll_interval=0.05,
    )
    return TaskLauncher(
        config,
        hooks=MagicMock(),
        circuit_breaker=CircuitBreakerPolicy(float("inf"), 2**31),
        fanout_manager=MagicMock(),
        promote_fn=MagicMock(),
    )


def _spin_until(predicate) -> bool:
    while not predicate():
        pass
    return True


class _FakeClock:
    def __init__(self, mono: float = 100.0, wall: float = 1_700_000_000.0) -> None:
        self._mono = mono
        self._wall = wall

    def monotonic(self) -> float:
        return self._mono

    def time(self) -> float:
        return self._wall

    def advance(self, sec: float) -> None:
        self._mono += sec
        self._wall += sec


def _group_gone(pgid: int) -> bool:
    return not _process_group_exists(pgid)


def _force_kill_pgid(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        pass


def _orphan_tree_command(script_path: Path, ready_path: Path) -> str:
    script_path.write_text(_ORPHAN_TREE_PY, encoding="utf-8")
    return f"python3 {script_path} {ready_path}"


def _read_done(done_dir: Path, uuid: str) -> str:
    return (done_dir / uuid).read_text(encoding="utf-8").strip()


def _ready_populated(ready: Path) -> bool:
    return ready.is_file() and bool(ready.read_text(encoding="utf-8").strip())


def _poll_until_done(
    launcher: TaskLauncher,
    uuid: str,
    *,
    clock: _FakeClock | None = None,
    freeze_after_term: bool = False,
) -> None:
    """Poll until done, advancing the fake clock per iteration.

    With ``freeze_after_term`` the clock stops once SIGTERM has been sent, so
    how long the real process takes to exit cannot push the fake clock past
    ``kill_grace``.
    """
    while not is_done(launcher._config.exec_done_dir, uuid):
        launcher.check_completions()
        if clock is None:
            continue
        rt = launcher._running.get(uuid)
        if freeze_after_term and rt is not None and rt.term_sent_at is not None:
            continue
        clock.advance(0.05)
    launcher.check_completions()


class TestProcessGroupExists:
    def test_alive_and_gone(self) -> None:
        proc = subprocess.Popen(
            ["sleep", "30"],
            start_new_session=True,
        )
        pgid = proc.pid
        try:
            assert _process_group_exists(pgid) is True
            os.killpg(pgid, signal.SIGKILL)
            proc.wait()
            assert _spin_until(lambda: not _process_group_exists(pgid))
            assert _process_group_exists(pgid) is False
        finally:
            _force_kill_pgid(pgid)
            proc.wait()


class TestSignalProcessTree:
    def test_process_lookup_error_is_ok(self) -> None:
        child = subprocess.Popen(["sleep", "30"], start_new_session=True)
        pgid = child.pid
        os.killpg(pgid, signal.SIGKILL)
        child.wait()
        assert _spin_until(lambda: not _process_group_exists(pgid))

        proc = MagicMock()
        proc.pid = pgid
        proc.poll.return_value = None
        _signal_process_tree(proc, signal.SIGTERM)
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()

    def test_refuses_runner_pgid(self) -> None:
        runner = os.getpgrp()
        proc = MagicMock()
        proc.pid = runner
        proc.poll.return_value = None
        with patch("ghdag.dag.task_launcher.os.killpg") as mock_killpg:
            _signal_process_tree(proc, signal.SIGTERM)
        mock_killpg.assert_not_called()
        proc.terminate.assert_called_once()


class TestRunningJsonPgid:
    def test_launch_pgid_matches_task_pid_not_runner(self, tmp_path: Path) -> None:
        launcher = _make_launcher(tmp_path, task_timeout=None)
        uuid = "pgid-launch"
        task = Task(uuid=uuid, command="sleep 30")
        pgid: int | None = None
        try:
            assert launcher.launch(uuid, task) is True
            rt = launcher._running[uuid]
            pgid = rt.proc.pid
            running_path = tmp_path / "jobs" / "running" / f"{uuid}.json"
            assert _spin_until(running_path.is_file)
            meta = json.loads(running_path.read_text(encoding="utf-8"))
            assert meta["pid"] == rt.proc.pid
            assert meta["pgid"] == rt.proc.pid
            assert meta["pgid"] != os.getpgrp()
            assert os.getpgid(rt.proc.pid) == rt.proc.pid
        finally:
            if pgid is not None:
                _force_kill_pgid(pgid)
            if uuid in launcher._running:
                launcher._running[uuid].proc.wait()
                del launcher._running[uuid]


class TestTimeoutKillsOrphanTree:
    def test_timeout_sigkills_sigterm_ignoring_grandchild(self, tmp_path: Path, monkeypatch) -> None:
        clock = _FakeClock(mono=100.0)
        monkeypatch.setattr("ghdag.dag.task_launcher.time", clock)
        launcher = _make_launcher(tmp_path, task_timeout=0.25, kill_grace=0.35)
        uuid = "timeout-orphan"
        ready = tmp_path / "ready-timeout"
        script = tmp_path / "orphan_timeout.py"
        task = Task(uuid=uuid, command=_orphan_tree_command(script, ready))
        pgid: int | None = None
        try:
            assert launcher.launch(uuid, task) is True
            rt = launcher._running[uuid]
            pgid = rt.proc.pid
            assert _spin_until(lambda: _ready_populated(ready))
            ready_text = ready.read_text(encoding="utf-8").strip()
            child_pid_s, child_pgid_s = ready_text.split()
            child_pid, child_pgid = int(child_pid_s), int(child_pgid_s)
            assert child_pgid == pgid

            _poll_until_done(launcher, uuid, clock=clock)

            assert is_done(launcher._config.exec_done_dir, uuid)
            assert _read_done(Path(launcher._config.exec_done_dir), uuid) == DONE_TIMEOUT
            assert _spin_until(lambda: _group_gone(pgid))
            with pytest.raises(ProcessLookupError):
                os.kill(child_pid, 0)
        finally:
            if pgid is not None:
                _force_kill_pgid(pgid)


class TestCancelKillsOrphanTree:
    def test_cancel_sigkills_sigterm_ignoring_grandchild(self, tmp_path: Path) -> None:
        launcher = _make_launcher(tmp_path, task_timeout=None, kill_grace=0.35)
        uuid = "cancel-orphan"
        ready = tmp_path / "ready-cancel"
        script = tmp_path / "orphan_cancel.py"
        task = Task(uuid=uuid, command=_orphan_tree_command(script, ready))
        pgid: int | None = None
        try:
            assert launcher.launch(uuid, task) is True
            rt = launcher._running[uuid]
            pgid = rt.proc.pid
            assert _spin_until(lambda: _ready_populated(ready))

            launcher.request_cancel(uuid)
            _poll_until_done(launcher, uuid)

            assert is_done(launcher._config.exec_done_dir, uuid)
            assert _read_done(Path(launcher._config.exec_done_dir), uuid) == DONE_CANCELLED
            assert _spin_until(lambda: _group_gone(pgid))
        finally:
            if pgid is not None:
                _force_kill_pgid(pgid)


class TestEarlyCompleteWithoutSigkill:
    def test_sigterm_alone_completes_before_kill_grace(self, tmp_path: Path, monkeypatch) -> None:
        """If the group vanishes on SIGTERM, do not wait for kill_grace or send SIGKILL."""
        clock = _FakeClock(mono=100.0)
        monkeypatch.setattr("ghdag.dag.task_launcher.time", clock)
        launcher = _make_launcher(tmp_path, task_timeout=0.2, kill_grace=5.0)
        uuid = "early-term"
        task = Task(uuid=uuid, command="sleep 60")
        pgid: int | None = None
        try:
            assert launcher.launch(uuid, task) is True
            pgid = launcher._running[uuid].proc.pid

            with patch("ghdag.dag.task_launcher.os.killpg", wraps=os.killpg) as mock_killpg:
                mono_at_poll = clock.monotonic()
                _poll_until_done(launcher, uuid, clock=clock, freeze_after_term=True)
                elapsed_mono = clock.monotonic() - mono_at_poll

            assert elapsed_mono < launcher._config.kill_grace
            assert is_done(launcher._config.exec_done_dir, uuid)
            assert _read_done(Path(launcher._config.exec_done_dir), uuid) == DONE_TIMEOUT
            # signal 0 probes may also call killpg; only require SIGTERM was used for stop.
            stop_signals = [c.args[1] for c in mock_killpg.call_args_list if c.args[1] != 0]
            assert signal.SIGTERM in stop_signals
            assert signal.SIGKILL not in stop_signals
        finally:
            if pgid is not None:
                _force_kill_pgid(pgid)


class TestLaunchStartNewSession:
    def test_popen_uses_start_new_session(self, tmp_path: Path) -> None:
        launcher = _make_launcher(tmp_path, task_timeout=None)
        fake = MagicMock()
        fake.pid = 424242
        fake.poll.return_value = None
        fake.stdout = None
        fake.stderr = MagicMock()
        with patch("ghdag.dag.task_launcher.subprocess.Popen", return_value=fake) as mock_popen:
            with patch("ghdag.dag.task_launcher.os.getpgid", return_value=424242):
                ok = launcher.launch("sns", Task(uuid="sns", command="true"))
        assert ok is True
        assert mock_popen.call_args.kwargs.get("start_new_session") is True
        del launcher._running["sns"]
