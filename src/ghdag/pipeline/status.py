"""pipeline/status.py — task state identifiers and display labels.

The status core lives in ``ghdag.status``. State values are language-neutral
identifiers (see :data:`ghdag.config.language.STATE_IDS`); use
:func:`state_label` to get the display string from the active language pack.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ghdag.config.language import LanguagePack, get_language_pack
from ghdag.io.done import dep_succeeded, interpret_done
from ghdag.io.done import read_done_content as read_done_content  # re-export for shim compat
from ghdag.status import _step_status_core

# State identifiers (keys of LanguagePack.state_labels)
STATE_PENDING_DEPS = "pending_deps"
STATE_PENDING_RUN  = "pending_run"
STATE_RUNNING      = "running"
STATE_DEFERRED     = "deferred"
STATE_OK           = "ok"
STATE_FAIL         = "fail"
STATE_REJECTED     = "rejected"
STATE_EMPTY        = "empty"
STATE_ENGINE_ERROR = "engine_error"
STATE_UNKNOWN_DONE = "unknown"


def state_label(state_id: str, pack: Optional[LanguagePack] = None) -> str:
    """Return the display label for *state_id* from *pack*.

    *pack* defaults to :func:`ghdag.config.language.get_language_pack`.
    Unknown identifiers are returned unchanged.
    """
    if pack is None:
        pack = get_language_pack()
    return pack.state_labels.get(state_id, state_id)


def label_for_done(raw: Optional[str]) -> Optional[str]:
    """Map raw done-marker content to a state identifier (``None`` if absent)."""
    if raw is None:
        return None
    kind = interpret_done(raw)
    if kind == "success":
        return STATE_OK
    if kind == "failed_exit":
        return STATE_FAIL
    if kind == "rejected":
        return STATE_REJECTED
    if kind == "empty_result":
        return STATE_EMPTY
    if kind == "engine_error":
        return STATE_ENGINE_ERROR
    return STATE_UNKNOWN_DONE


_CORE_TO_STATE_ID = {
    "success": STATE_OK,
    "failed_exit": STATE_FAIL,
    "rejected": STATE_REJECTED,
    "empty_result": STATE_EMPTY,
    "engine_error": STATE_ENGINE_ERROR,
    "other": STATE_UNKNOWN_DONE,
    "cancelled": STATE_UNKNOWN_DONE,
    "skipped": STATE_UNKNOWN_DONE,
    "running": STATE_RUNNING,
    "deferred": STATE_DEFERRED,
    "dep_failed": STATE_PENDING_DEPS,
}


def task_status(
    uuid: str,
    exec_done_dir: Path,
    *,
    task_depends: set[str] | None = None,
    running_uuids: set[str] | None = None,
    deferred_uuids: set[str] | None = None,
) -> str:
    """Determine the task's current state and return its state identifier.

    The status core is ``ghdag.status._step_status_core`` (shared with the UI
    and issue_status). Use :func:`state_label` for the display string.
    """
    core = _step_status_core(
        uuid,
        exec_done_dir,
        depends=task_depends,
        running_uuids=running_uuids,
        deferred_uuids=deferred_uuids,
    )
    if core in _CORE_TO_STATE_ID:
        return _CORE_TO_STATE_ID[core]
    if core == "pending":
        # Distinguish ready-to-run vs waiting on incomplete (non-failed) deps.
        if task_depends:
            for d in task_depends:
                if not dep_succeeded(exec_done_dir, d):
                    return STATE_PENDING_DEPS
        return STATE_PENDING_RUN
    if core == "failed":
        return STATE_FAIL
    return STATE_UNKNOWN_DONE
