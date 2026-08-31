"""Manage task completion state via the done directory (jobs/done/)."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

from ghdag.core.vocabulary import DONE_SUCCESS


def is_done(exec_done_dir: str | Path, uuid: str) -> bool:
    """Return True if the task has a completion marker."""
    return os.path.exists(os.path.join(str(exec_done_dir), uuid))


def mark_done(exec_done_dir: str | Path, uuid: str, status: str | int) -> None:
    """Write a completion marker for the given task."""
    d = str(exec_done_dir)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, uuid), "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            f.truncate()
            f.write(str(status))
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load_done_from_dir(exec_done_dir: str | Path) -> set[str]:
    """Return all completed UUIDs (regardless of success/failure)."""
    d = str(exec_done_dir)
    if not os.path.isdir(d):
        return set()
    return set(os.listdir(d))


def load_succeeded_from_dir(exec_done_dir: str | Path) -> set[str]:
    """Return UUIDs that succeeded (status '0' or empty string)."""
    d = str(exec_done_dir)
    if not os.path.isdir(d):
        return set()
    succeeded = set()
    for uuid in os.listdir(d):
        try:
            content = open(os.path.join(d, uuid)).read().strip()
            if content == DONE_SUCCESS or content == "":
                succeeded.add(uuid)
        except OSError:
            pass
    return succeeded
