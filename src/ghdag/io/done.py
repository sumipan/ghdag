"""ghdag.io.done — unified done-marker I/O (nexus Issue #2675)."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import Optional

from ghdag.core.vocabulary import (
    DONE_EMPTY_RESULT,
    DONE_ENGINE_ENV_ERROR,
    DONE_ENGINE_ERROR,
    DONE_ENGINE_ERROR_FINAL,
    DONE_REJECTED,
    DONE_REJECTED_FINAL,
    DONE_SUCCESS,
)


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


def interpret_done(raw: Optional[str]) -> Optional[str]:
    """Interpret a done-marker body as a coarse outcome string."""
    if raw is None:
        return None
    first = raw.strip().splitlines()
    c = first[0].strip() if first else ""
    if c == "" or c == DONE_SUCCESS:
        return "success"
    if c in (DONE_REJECTED, DONE_REJECTED_FINAL):
        return "rejected"
    if c == DONE_EMPTY_RESULT:
        return "empty_result"
    if c in (DONE_ENGINE_ERROR, DONE_ENGINE_ERROR_FINAL, DONE_ENGINE_ENV_ERROR):
        return "engine_error"
    try:
        n = int(c)
        return "success" if n == 0 else "failed_exit"
    except ValueError:
        return "other"


def read_done_content(exec_done_dir: Path, uuid: str) -> Optional[str]:
    """jobs/done/<uuid> の内容を読み取る。存在しなければ None。"""
    p = Path(exec_done_dir) / uuid
    if not p.is_file():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def dep_succeeded(exec_done_dir: Path, dep_uuid: str) -> bool:
    """依存タスクが成功完了しているか。

    ``interpret_done`` の success 判定と同等（io 層で完結）。
    """
    return interpret_done(read_done_content(exec_done_dir, dep_uuid)) == "success"
