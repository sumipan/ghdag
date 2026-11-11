"""ghdag.core.stall_guard is the canonical home; ghdag.dag.stall_guard re-exports it."""

from __future__ import annotations

import signal

import pytest

import ghdag.core.stall_guard as core_sg
import ghdag.dag.stall_guard as dag_sg
import ghdag.llm.engines as engines
from ghdag.core.stall_guard import StallTracker


@pytest.mark.parametrize(
    "name",
    [
        "ProcInfo",
        "StallEvent",
        "StallTracker",
        "find_stalled_state_readers",
        "parse_ps_output",
        "scan_process_tree",
    ],
)
def test_dag_reexports_core_objects(name: str) -> None:
    assert getattr(dag_sg, name) is getattr(core_sg, name)


def test_dag_read_ps_is_engines_read_ps() -> None:
    # ps runs in a subprocess, which core's purity guard forbids.
    assert dag_sg.read_ps is engines.read_ps


def test_dag_all_matches_exports() -> None:
    assert sorted(dag_sg.__all__) == sorted(
        [
            "ProcInfo",
            "StallEvent",
            "StallTracker",
            "find_stalled_state_readers",
            "parse_ps_output",
            "read_ps",
            "scan_process_tree",
        ]
    )
    for name in dag_sg.__all__:
        assert hasattr(dag_sg, name)


def _grep_ps_text(
    *,
    grep_time: str,
    zsh_args: str = "zsh -c snap=$(command cat <&3); eval snap",
    grep_args: str = "grep -rl pattern /tmp/x",
    root_pid: int = 100,
    grep_pid: int = 400,
    zsh_pid: int = 200,
) -> str:
    agent_line = f"  150 {root_pid} 0:01.00 agent agent --model composer"
    zsh_line = f"  {zsh_pid} 150 0:00.10 /bin/zsh {zsh_args}"
    grep_line = f"  {grep_pid} {zsh_pid} {grep_time} /usr/bin/grep {grep_args}"
    return "\n".join([agent_line, zsh_line, grep_line]).strip() + "\n"


def test_recursive_grep_killed_once_when_cpu_time_frozen() -> None:
    """sumipan/nexus#6350: frozen CPU time on recursive grep under snap shell."""
    kills: list[tuple[int, int]] = []
    grep_pid = 400

    def fake_ps() -> str:
        return _grep_ps_text(grep_time="0:00.42")

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
    assert events[0].pid == grep_pid
    assert kills == [(grep_pid, signal.SIGTERM)]
    tracker.check(100.0)
    assert kills == [(grep_pid, signal.SIGTERM)]


def test_recursive_grep_not_killed_while_cpu_time_advances() -> None:
    """sumipan/nexus#6350: advancing grep CPU time resets the stall clock."""
    kills: list[tuple[int, int]] = []
    times = [f"0:00.{10 + i:02d}" for i in range(21)]

    def fake_ps() -> str:
        idx = min(fake_ps.i, len(times) - 1)
        fake_ps.i += 1
        return _grep_ps_text(grep_time=times[idx])

    fake_ps.i = 0  # type: ignore[attr-defined]

    tracker = StallTracker(
        100,
        stall_sec=5.0,
        interval_sec=0.0,
        kill=lambda pid, sig: kills.append((pid, sig)),
        ps=fake_ps,
    )
    for t in range(21):
        tracker.check(float(t))
    assert kills == []


def test_non_recursive_grep_not_killed() -> None:
    """sumipan/nexus#6350: plain grep without recursive flags is not a stall target."""
    kills: list[tuple[int, int]] = []

    def fake_ps() -> str:
        return _grep_ps_text(grep_time="0:00.42", grep_args="grep x")

    tracker = StallTracker(
        100,
        stall_sec=2.0,
        interval_sec=0.0,
        kill=lambda pid, sig: kills.append((pid, sig)),
        ps=fake_ps,
    )
    tracker.check(0.0)
    tracker.check(10.0)
    assert kills == []


def test_grep_without_snap_marker_parent_not_killed() -> None:
    """sumipan/nexus#6350: recursive grep without snap parent is not a stall target."""
    kills: list[tuple[int, int]] = []

    def fake_ps() -> str:
        return _grep_ps_text(
            grep_time="0:00.42",
            zsh_args="zsh -c echo hi",
            grep_args="grep -r x /tmp/x",
        )

    tracker = StallTracker(
        100,
        stall_sec=2.0,
        interval_sec=0.0,
        kill=lambda pid, sig: kills.append((pid, sig)),
        ps=fake_ps,
    )
    tracker.check(0.0)
    tracker.check(10.0)
    assert kills == []
