"""pipeline/status.py — タスク状態判定ロジック（ui/monitor.py から移動）"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ghdag.io.done import dep_succeeded, interpret_done, read_done_content

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


def task_status(
    uuid: str,
    exec_done_dir: Path,
    *,
    task_depends: set[str] | None = None,
    running_uuids: set[str] | None = None,
    deferred_uuids: set[str] | None = None,
) -> str:
    """タスクの現在状態を判定して状態定数を返す。

    1. jobs/done/<uuid> が存在 → label_for_done で完了状態を判定
    2. 依存タスクが未完了 → STATE_PENDING_DEPS
    3. running_uuids に含まれる → STATE_RUNNING
    4. それ以外 → STATE_PENDING_RUN
    """
    raw = read_done_content(exec_done_dir, uuid)
    if raw is not None:
        lbl = label_for_done(raw)
        return lbl if lbl else STATE_UNKNOWN_DONE

    if running_uuids and uuid in running_uuids:
        return STATE_RUNNING

    if deferred_uuids and uuid in deferred_uuids:
        return STATE_DEFERRED

    if task_depends:
        for d in task_depends:
            if not dep_succeeded(exec_done_dir, d):
                return STATE_PENDING_DEPS

    return STATE_PENDING_RUN
