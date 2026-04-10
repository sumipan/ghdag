"""Manage task completion state via the exec-done directory."""

from __future__ import annotations

import os
from pathlib import Path


def is_done(exec_done_dir: str | Path, uuid: str) -> bool:
    """Return True if the task has a completion marker."""
    return os.path.exists(os.path.join(str(exec_done_dir), uuid))


def mark_done(exec_done_dir: str | Path, uuid: str, status: str | int) -> None:
    """Write a completion marker for the given task."""
    d = str(exec_done_dir)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, uuid), "w") as f:
        f.write(str(status))


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
            if content == "0" or content == "":
                succeeded.add(uuid)
        except OSError:
            pass
    return succeeded
