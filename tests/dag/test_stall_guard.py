"""Tests for cursor engine stall guard (issue #4931)."""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import time

import pytest

from ghdag.config import env as env_mod
from ghdag.dag.stall_guard import (
    StallTracker,
    find_stalled_state_readers,
    parse_ps_output,
    scan_process_tree,
)


def _sample_ps_text(
    *,
    cat_time: str = "0:00.00",
    cat_ppid: int = 200,
    root_pid: int = 100,
    extra_lines: str = "",
) -> str:
    agent_line = f"  150 {root_pid} 0:01.00 agent agent --model composer"
    zsh_line = (
        f"  {cat_ppid} 150 {cat_time} /bin/zsh "
        "zsh -c snap=$(command cat <&3); eval snap"
    )
    cat_line = f"  300 {cat_ppid} {cat_time} /bin/cat cat"
    return "\n".join([agent_line, zsh_line, cat_line, extra_lines]).strip() + "\n"


class TestScanAndFindStalled:
    def test_finds_stalled_cat_under_zsh(self) -> None:
        text = _sample_ps_text()
        tree = scan_process_tree(100, text)
        assert find_stalled_state_readers(tree) == [300]

    def test_nonzero_time_cat_ignored(self) -> None:
        text = _sample_ps_text(cat_time="0:00.01")
        tree = scan_process_tree(100, text)
        assert find_stalled_state_readers(tree) == []

    def test_parent_node_cat_ignored(self) -> None:
        text = (
            "  100    1 0:00.00 bash bash\n"
            "  150  100 0:00.00 node node agent\n"
            "  300  150 0:00.00 /bin/cat cat\n"
        )
        tree = scan_process_tree(100, text)
        assert find_stalled_state_readers(tree) == []

    def test_missing_snap_marker_ignored(self) -> None:
        text = (
            "  100    1 0:00.00 bash bash\n"
            "  200  100 0:00.00 /bin/zsh zsh -c echo hi\n"
            "  300  200 0:00.00 /bin/cat cat\n"
        )
        tree = scan_process_tree(100, text)
        assert find_stalled_state_readers(tree) == []

    def test_cat_outside_tree_ignored(self) -> None:
        text = _sample_ps_text(
            extra_lines="  999    1 0:00.00 /bin/cat cat",
        )
        tree = scan_process_tree(100, text)
        assert find_stalled_state_readers(tree) == [300]

    def test_linux_zero_time_format(self) -> None:
        text = (
            "  100    1 00:00:00 bash bash\n"
            "  200  100 00:00:00 /bin/zsh zsh -c snap=$(command cat <&3); x\n"
            "  300  200 00:00:00 /bin/cat cat\n"
        )
        tree = scan_process_tree(100, text)
        assert find_stalled_state_readers(tree) == [300]


class TestParsePsOutput:
    def test_skips_malformed_lines(self) -> None:
        text = (
            "not enough cols\n"
            "abc 1 0:00.00 cat cat\n"
            "  10  1 0:00.00 cat cat\n"
        )
        procs = parse_ps_output(text)
        assert len(procs) == 1
        assert procs[0].pid == 10


class TestStallTracker:
    def test_kill_after_stall_sec_once(self) -> None:
        kills: list[tuple[int, int]] = []
        ps_calls = 0

        def fake_ps() -> str:
            nonlocal ps_calls
            ps_calls += 1
            return _sample_ps_text()

        tracker = StallTracker(
            100,
            stall_sec=5.0,
            interval_sec=0.0,
            kill=lambda pid, sig: kills.append((pid, sig)),
            ps=fake_ps,
        )
        assert tracker.check(0.0) == []
        assert kills == []
        assert tracker.check(4.0) == []
        assert kills == []
        events = tracker.check(5.0)
        assert len(events) == 1
        assert events[0].pid == 300
        assert kills == [(300, signal.SIGTERM)]
        tracker.check(100.0)
        assert kills == [(300, signal.SIGTERM)]

    def test_reset_when_cat_disappears(self) -> None:
        kills: list[tuple[int, int]] = []
        show_cat = True

        def fake_ps() -> str:
            if show_cat:
                return _sample_ps_text()
            return "  100    1 0:00.00 bash bash\n  150  100 0:00.00 agent agent\n"

        tracker = StallTracker(
            100,
            stall_sec=2.0,
            interval_sec=0.0,
            kill=lambda pid, sig: kills.append((pid, sig)),
            ps=fake_ps,
        )
        tracker.check(0.0)
        show_cat = False
        tracker.check(1.0)
        show_cat = True
        tracker.check(2.0)
        assert kills == []
        tracker.check(4.0)
        assert kills == [(300, signal.SIGTERM)]

    def test_ps_failure_logs_and_returns_empty(self, caplog: pytest.LogCaptureFixture) -> None:
        def bad_ps() -> str:
            raise OSError("nope")

        tracker = StallTracker(
            100,
            stall_sec=1.0,
            interval_sec=0.0,
            ps=bad_ps,
        )
        with caplog.at_level("WARNING"):
            assert tracker.check(0.0) == []
        assert any("[stall-guard]" in r.message for r in caplog.records)


class TestStallGuardEnv:
    def test_enabled_default_and_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GHDAG_ENGINE_STALL_GUARD", raising=False)
        assert env_mod.stall_guard_enabled() is True
        monkeypatch.setenv("GHDAG_ENGINE_STALL_GUARD", "0")
        assert env_mod.stall_guard_enabled() is False

    def test_stall_sec(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GHDAG_ENGINE_STALL_SEC", raising=False)
        assert env_mod.stall_guard_stall_sec() == 180.0
        monkeypatch.setenv("GHDAG_ENGINE_STALL_SEC", "abc")
        assert env_mod.stall_guard_stall_sec() == 180.0
        monkeypatch.setenv("GHDAG_ENGINE_STALL_SEC", "-1")
        assert env_mod.stall_guard_stall_sec() == 180.0
        monkeypatch.setenv("GHDAG_ENGINE_STALL_SEC", "5")
        assert env_mod.stall_guard_stall_sec() == 5.0

    def test_interval_sec(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GHDAG_ENGINE_STALL_INTERVAL_SEC", raising=False)
        assert env_mod.stall_guard_interval_sec() == 30.0
        monkeypatch.setenv("GHDAG_ENGINE_STALL_INTERVAL_SEC", "abc")
        assert env_mod.stall_guard_interval_sec() == 30.0
        monkeypatch.setenv("GHDAG_ENGINE_STALL_INTERVAL_SEC", "-1")
        assert env_mod.stall_guard_interval_sec() == 30.0


def _run_socketpair_integration(shell: str) -> None:
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} not available")
    sock_a, sock_b = socket.socketpair()
    kills: list[tuple[int, int, str]] = []

    def recording_kill(pid: int, sig: int) -> None:
        kills.append((pid, sig, _comm_for_pid(pid)))
        os.kill(pid, sig)

    sock_fd = sock_a.fileno()
    inner = f"exec 3<&{sock_fd}; snap=$(command cat <&3); echo DONE"
    proc = subprocess.Popen(
        [shell, "-c", inner],
        pass_fds=(sock_fd,),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    sock_a.close()
    from ghdag.dag.stall_guard import read_ps

    tracker = StallTracker(
        proc.pid,
        stall_sec=1.0,
        interval_sec=0.0,
        kill=recording_kill,
        ps=read_ps,
    )
    deadline = time.monotonic() + 15.0
    try:
        while time.monotonic() < deadline:
            tracker.check(time.monotonic())
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        stdout, _stderr = proc.communicate(timeout=5)
        assert proc.returncode == 0
        assert b"DONE" in stdout
        term_kills = [entry for entry in kills if entry[1] == signal.SIGTERM]
        assert term_kills
        assert all(entry[2] == "cat" for entry in term_kills)
        assert proc.pid not in {entry[0] for entry in term_kills}
    finally:
        sock_b.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def _comm_for_pid(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "comm="],
        capture_output=True,
        text=True,
        check=False,
    )
    return os.path.basename(result.stdout.strip())


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_integration_socketpair_unblocks_cat(shell: str) -> None:
    _run_socketpair_integration(shell)
