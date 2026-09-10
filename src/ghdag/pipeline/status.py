"""pipeline/status.py — タスク状態の日本語表示（判定コアは ghdag.status）"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ghdag.io.done import dep_succeeded, interpret_done, read_done_content
from ghdag.status import _step_status_core

# 状態定数
STATE_PENDING_DEPS = "待機（依存未充足）"
STATE_PENDING_RUN  = "待機（実行可能）"
STATE_RUNNING      = "実行中"
STATE_DEFERRED     = "保留（DEFERRED）"
STATE_OK           = "完了（成功）"
STATE_FAIL         = "完了（失敗）"
STATE_REJECTED     = "完了（REJECTED）"
STATE_EMPTY        = "完了（EMPTY_RESULT）"
STATE_ENGINE_ERROR = "完了（ENGINE_ERROR）"
STATE_UNKNOWN_DONE = "完了（その他）"


def label_for_done(raw: Optional[str]) -> Optional[str]:
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


_CORE_TO_JP = {
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
    """タスクの現在状態を判定して日本語状態定数を返す。

    判定コアは ``ghdag.status._step_status_core``（UI / issue_status と共有）。
    """
    core = _step_status_core(
        uuid,
        exec_done_dir,
        depends=task_depends,
        running_uuids=running_uuids,
        deferred_uuids=deferred_uuids,
    )
    if core in _CORE_TO_JP:
        return _CORE_TO_JP[core]
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
