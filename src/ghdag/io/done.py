"""ghdag.io.done — unified done-marker I/O."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Optional

from ghdag.core.models.metrics import FailureClass
from ghdag.core.vocabulary import (
    DONE_CANCELLED,
    DONE_DEP_FAILED,
    DONE_EMPTY_RESULT,
    DONE_ENGINE_ENV_ERROR,
    DONE_ENGINE_ERROR,
    DONE_ENGINE_ERROR_FINAL,
    DONE_FANOUT_CHILD_FAILED,
    DONE_FANOUT_PARSE_FAILED,
    DONE_INTERACTIVE_PROMPT,
    DONE_ORPHAN_ARCHIVED,
    DONE_ORPHANED_ON_RESTART,
    DONE_PIPELINE_FAILED_PREFIX,
    DONE_REJECTED,
    DONE_REJECTED_FINAL,
    DONE_SKIPPED_MISSING_INPUT,
    DONE_SUCCESS,
    DONE_TIMEOUT,
    DONE_UNKNOWN_FAILURE,
)

__all__ = [
    "dep_succeeded",
    "done_failure_class",
    "interpret_done",
    "is_done",
    "load_done_from_dir",
    "load_succeeded_from_dir",
    "mark_done",
    "read_done_content",
]

_MARKER_FAILURE_CLASS: dict[str, FailureClass] = {
    DONE_REJECTED: FailureClass.REJECTED,
    DONE_REJECTED_FINAL: FailureClass.REJECTED,
    DONE_ENGINE_ERROR: FailureClass.ENGINE_ERROR,
    DONE_ENGINE_ERROR_FINAL: FailureClass.ENGINE_ERROR,
    DONE_ENGINE_ENV_ERROR: FailureClass.ENGINE_ENVIRONMENT_ERROR,
    DONE_INTERACTIVE_PROMPT: FailureClass.INTERACTIVE_PROMPT,
    DONE_TIMEOUT: FailureClass.TIMEOUT,
    DONE_EMPTY_RESULT: FailureClass.EMPTY_RESULT,
    DONE_FANOUT_CHILD_FAILED: FailureClass.FANOUT_CHILD_FAILED,
    DONE_FANOUT_PARSE_FAILED: FailureClass.FANOUT_PARSE_FAILED,
    DONE_DEP_FAILED: FailureClass.DEP_FAILED,
    DONE_UNKNOWN_FAILURE: FailureClass.UNKNOWN_FAILURE,
}

_STATE_MARKERS_WITHOUT_FAILURE_CLASS = frozenset(
    {
        DONE_CANCELLED,
        DONE_SKIPPED_MISSING_INPUT,
        DONE_ORPHAN_ARCHIVED,
        DONE_ORPHANED_ON_RESTART,
    }
)


def is_done(exec_done_dir: str | Path, uuid: str) -> bool:
    """Return True if the task has a completion marker."""
    return os.path.exists(os.path.join(str(exec_done_dir), uuid))


def _format_done_body(status: str | int) -> str:
    """Serialize a done-marker body (success → ``0``, other statuses unchanged)."""
    if status == 0 or str(status) == DONE_SUCCESS:
        return DONE_SUCCESS
    return str(status)


def mark_done(exec_done_dir: str | Path, uuid: str, status: str | int) -> None:
    """Write a completion marker for the given task."""
    d = str(exec_done_dir)
    os.makedirs(d, exist_ok=True)
    body = _format_done_body(status)
    with open(os.path.join(d, uuid), "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            f.truncate()
            f.write(body)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load_done_from_dir(exec_done_dir: str | Path) -> set[str]:
    """Return all completed UUIDs (regardless of success/failure)."""
    d = str(exec_done_dir)
    if not os.path.isdir(d):
        return set()
    return set(os.listdir(d))


def load_succeeded_from_dir(exec_done_dir: str | Path) -> set[str]:
    """Return UUIDs whose done marker interprets as success."""
    d = str(exec_done_dir)
    if not os.path.isdir(d):
        return set()
    succeeded = set()
    for uuid in os.listdir(d):
        try:
            content = open(os.path.join(d, uuid)).read().strip()
            if interpret_done(content) == "success":
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
    if c == "":
        return "other"
    if c == DONE_SUCCESS:
        return "success"
    if c.startswith("{"):
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(obj, dict) and obj.get("status") == "ok":
                exit_code = obj.get("exit_code", obj.get("exitCode"))
                if exit_code in (0, None):
                    return "success"
            return "other"
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
    """Read the contents of jobs/done/<uuid>. None if it does not exist."""
    p = Path(exec_done_dir) / uuid
    if not p.is_file():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def done_failure_class(raw: str | None) -> FailureClass | None:
    """Map a done-marker body to :class:`FailureClass`, if applicable."""
    if raw is None:
        return None
    lines = raw.strip().splitlines()
    first = lines[0].strip() if lines else ""
    if first == "":
        return FailureClass.UNKNOWN_FAILURE
    if first == DONE_SUCCESS:
        return None
    if first.startswith("{"):
        try:
            obj = json.loads(first)
        except json.JSONDecodeError:
            return FailureClass.UNKNOWN_FAILURE
        if isinstance(obj, dict) and obj.get("status") == "ok":
            exit_code = obj.get("exit_code", obj.get("exitCode"))
            if exit_code in (0, None):
                return None
        return FailureClass.UNKNOWN_FAILURE
    if first in _STATE_MARKERS_WITHOUT_FAILURE_CLASS:
        return None
    if first.startswith(DONE_PIPELINE_FAILED_PREFIX):
        return FailureClass.PIPELINE_FAILED
    marker = _MARKER_FAILURE_CLASS.get(first)
    if marker is not None:
        return marker
    try:
        exit_code = int(first)
    except ValueError:
        return FailureClass.UNKNOWN_FAILURE
    if exit_code == 0:
        return None
    return FailureClass.PROCESS_ERROR


def dep_succeeded(exec_done_dir: Path, dep_uuid: str) -> bool:
    """Whether the dependency task has completed successfully.

    Equivalent to the success check in ``interpret_done`` (self-contained in the io layer).
    """
    return interpret_done(read_done_content(exec_done_dir, dep_uuid)) == "success"
