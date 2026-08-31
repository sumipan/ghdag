"""Parse exec.jsonl into a list of Tasks."""

from __future__ import annotations

from collections import deque

from ghdag.io.exec_jsonl import parse as parse_jsonl

from .models import Task

__all__ = ["parse_jsonl", "validate_dependencies"]


def validate_dependencies(
    tasks: list[Task],
    done: set[str],
) -> dict[str, str]:
    """依存グラフを検証し、問題のあるタスクの UUID と理由を返す。

    Args:
        tasks: パース済みタスクリスト
        done: 完了済み UUID の集合（jobs/done/ から取得）

    Returns:
        {uuid: reason} の辞書。reason は "orphan_dep:<missing_uuid>" または "cycle"
    """
    task_uuids = {t.uuid for t in tasks}
    all_known = task_uuids | done

    failed: dict[str, str] = {}

    # Orphan dependency check: dep not in exec.jsonl and not in done
    for task in tasks:
        for dep in task.depends:
            if dep not in all_known:
                failed[task.uuid] = f"orphan_dep:{dep}"
                break

    # Cycle detection via Kahn's algorithm (topological sort)
    # Only consider deps that are within the current task set (not done)
    in_degree: dict[str, int] = {uid: 0 for uid in task_uuids}
    dependents: dict[str, list[str]] = {uid: [] for uid in task_uuids}

    for task in tasks:
        for dep in task.depends:
            if dep in task_uuids:
                in_degree[task.uuid] += 1
                dependents[dep].append(task.uuid)

    queue: deque[str] = deque(uid for uid in task_uuids if in_degree[uid] == 0)
    processed: set[str] = set()

    while queue:
        uid = queue.popleft()
        processed.add(uid)
        for dependent in dependents[uid]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # Nodes not processed could not be topologically sorted — they are in cycles
    for uid in task_uuids:
        if uid not in processed:
            failed[uid] = "cycle"

    return failed
