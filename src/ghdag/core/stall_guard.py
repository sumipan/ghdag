"""Detect and unblock cursor-agent shell state-transfer stalls (stuck ``cat`` readers).

Pure logic only: the ``ps`` reader is injected (``ghdag.llm.engines.read_ps`` in
production) so core stays free of subprocess IO.
"""

from __future__ import annotations

import logging
import os
import re
import signal
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_STATE_SNAP_MARKER = "snap=$(command cat <&3)"
# GNU ps on Linux truncates the args column; the snap prefix still appears in snapshots.
_STATE_SNAP_PREFIX = "snap=$(command cat"


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    ppid: int
    cpu_time_zero: bool
    comm: str
    args: str
    cpu_time: str = ""


@dataclass(frozen=True)
class StallEvent:
    pid: int
    stalled_sec: float
    parent_args_head: str


_RECURSIVE_SHORT_OPT = re.compile(r"^-[A-Za-z0-9]*[rR][A-Za-z0-9]*$")


def _has_recursive_flag(args: str) -> bool:
    tokens = args.split()
    if not tokens:
        return False
    for token in tokens[1:]:
        if token == "--":
            break
        if token in ("--recursive", "--dereference-recursive"):
            return True
        if _RECURSIVE_SHORT_OPT.match(token):
            return True
    return False


def _shell_has_state_snap_marker(args: str) -> bool:
    if _STATE_SNAP_MARKER in args:
        return True
    return _STATE_SNAP_PREFIX in args


def _cpu_time_is_zero(time_col: str) -> bool:
    stripped = time_col.strip()
    if not stripped:
        return False
    if stripped == "-":
        return True
    for ch in stripped:
        if ch not in "0:.":
            return False
    return True


def parse_ps_output(text: str) -> list[ProcInfo]:
    """Parse ``ps -axo pid=,ppid=,time=,comm=,args=`` lines into :class:`ProcInfo`."""
    out: list[ProcInfo] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        time_col = parts[2]
        comm_raw = parts[3]
        args = parts[4] if len(parts) > 4 else comm_raw
        comm = os.path.basename(comm_raw)
        out.append(
            ProcInfo(
                pid=pid,
                ppid=ppid,
                cpu_time_zero=_cpu_time_is_zero(time_col),
                comm=comm,
                args=args,
                cpu_time=parts[2].strip(),
            )
        )
    return out


def scan_process_tree(root_pid: int, ps_output: str) -> list[ProcInfo]:
    """Return descendants of *root_pid* (excluding the root itself)."""
    procs = parse_ps_output(ps_output)
    by_ppid: dict[int, list[ProcInfo]] = {}
    for proc in procs:
        by_ppid.setdefault(proc.ppid, []).append(proc)

    descendants: list[ProcInfo] = []
    stack = list(by_ppid.get(root_pid, []))
    while stack:
        proc = stack.pop()
        descendants.append(proc)
        stack.extend(by_ppid.get(proc.pid, []))
    return descendants


def find_stalled_state_readers(
    procs: list[ProcInfo],
    *,
    all_procs: list[ProcInfo] | None = None,
) -> list[int]:
    """Return PIDs of ``cat`` processes stuck reading cursor shell state snapshots."""
    by_pid = {p.pid: p for p in (all_procs if all_procs is not None else procs)}
    stalled: list[int] = []
    for proc in procs:
        if proc.comm != "cat" or not proc.cpu_time_zero:
            continue
        parent = by_pid.get(proc.ppid)
        if parent is None:
            continue
        if parent.comm not in ("zsh", "bash"):
            continue
        if not _shell_has_state_snap_marker(parent.args):
            continue
        stalled.append(proc.pid)
    return stalled


def find_stalled_recursive_greps(
    procs: list[ProcInfo],
    *,
    all_procs: list[ProcInfo] | None = None,
) -> list[int]:
    """Return PIDs of recursive ``grep`` children under cursor state-snapshot shells."""
    by_pid = {p.pid: p for p in (all_procs if all_procs is not None else procs)}
    stalled: list[int] = []
    for proc in procs:
        if proc.comm != "grep" or not _has_recursive_flag(proc.args):
            continue
        parent = by_pid.get(proc.ppid)
        if parent is None:
            continue
        if parent.comm not in ("zsh", "bash"):
            continue
        if not _shell_has_state_snap_marker(parent.args):
            continue
        stalled.append(proc.pid)
    return stalled


class StallTracker:
    """Track stalled ``cat`` / recursive ``grep`` children and SIGTERM them once."""

    def __init__(
        self,
        root_pid: int,
        *,
        stall_sec: float,
        interval_sec: float,
        ps: Callable[[], str],
        kill: Callable[[int, int], None] = os.kill,
    ) -> None:
        self._root_pid = root_pid
        self._stall_sec = stall_sec
        self._interval_sec = interval_sec
        self._kill = kill
        self._ps = ps
        self._last_check_mono: float | None = None
        self._first_seen: dict[int, float] = {}
        self._last_cpu: dict[int, str] = {}
        self._killed: set[int] = set()
        self.events: list[StallEvent] = []

    def check(self, now: float) -> list[StallEvent]:
        if self._last_check_mono is not None:
            if (now - self._last_check_mono) < self._interval_sec:
                return []
        self._last_check_mono = now

        try:
            ps_text = self._ps()
        except Exception as exc:  # a failing ps must never break the caller
            logger.warning("[stall-guard] ps failed: %s", exc)
            return []

        all_procs = parse_ps_output(ps_text)
        tree = scan_process_tree(self._root_pid, ps_text)
        stalled_cat_pids = set(find_stalled_state_readers(tree, all_procs=all_procs))
        stalled_grep_pids = set(find_stalled_recursive_greps(tree, all_procs=all_procs))
        stalled_pids = stalled_cat_pids | stalled_grep_pids
        for pid in list(self._first_seen):
            if pid not in stalled_pids:
                del self._first_seen[pid]
        for pid in list(self._last_cpu):
            if pid not in stalled_grep_pids:
                del self._last_cpu[pid]

        for pid in stalled_grep_pids:
            grep_proc = next((p for p in tree if p.pid == pid), None)
            if grep_proc is None:
                continue
            cpu = grep_proc.cpu_time
            if pid not in self._last_cpu:
                self._last_cpu[pid] = cpu
            elif self._last_cpu[pid] != cpu:
                self._last_cpu[pid] = cpu
                self._first_seen[pid] = now

        by_pid = {p.pid: p for p in tree}
        new_events: list[StallEvent] = []
        for pid in stalled_pids:
            if pid not in self._first_seen:
                self._first_seen[pid] = now
            stalled_for = now - self._first_seen[pid]
            if stalled_for < self._stall_sec or pid in self._killed:
                continue
            try:
                self._kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            self._killed.add(pid)
            cat_proc = next((p for p in tree if p.pid == pid), None)
            parent = by_pid.get(cat_proc.ppid) if cat_proc is not None else None
            parent_args = parent.args if parent is not None else ""
            event = StallEvent(
                pid=pid,
                stalled_sec=stalled_for,
                parent_args_head=parent_args[:80],
            )
            self.events.append(event)
            new_events.append(event)
        return new_events
