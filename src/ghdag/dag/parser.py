"""Parse exec.md into a list of Tasks."""

from __future__ import annotations

import fcntl
import json
import logging
import re
from collections import deque
from pathlib import Path

from .models import Task

logger = logging.getLogger(__name__)

_LINE_RE = re.compile(r"^([a-zA-Z0-9\-]+)((?:\[[^\]]+\])*)\s*:\s*(.+)$")
_DEPENDS_RE = re.compile(r"\[depends:([^\]]+)\]")
_RETRY_RE = re.compile(r"\[retry:(\d+)\]")
_ANNOTATION_RE = re.compile(r"\[([^:\]]+):([^\]]+)\]")


def parse_exec_md(exec_md_path: str | Path) -> list[Task]:
    """Parse exec.md and return a list of Tasks.

    Blank lines and comment lines (#) are skipped.
    Unparseable lines emit a warning and are skipped.
    Uses LOCK_SH to prevent reading partially-written files.
    """
    path = Path(exec_md_path)
    with open(path, encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            lines = f.readlines()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    tasks: list[Task] = []
    seen: set[str] = set()

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        m = _LINE_RE.match(line)
        if not m:
            logger.warning("Skipping unparseable line: %s", line)
            continue

        uuid = m.group(1).strip()
        annotation_str = m.group(2)
        command = m.group(3).strip()

        depends_m = _DEPENDS_RE.search(annotation_str)
        depends = [d.strip() for d in depends_m.group(1).split(",")] if depends_m else []

        retry_m = _RETRY_RE.search(annotation_str)
        retry = int(retry_m.group(1)) if retry_m else 0

        annotations: dict[str, str] = {}
        for am in _ANNOTATION_RE.finditer(annotation_str):
            key = am.group(1).strip()
            val = am.group(2).strip()
            if key not in ("depends", "retry"):
                annotations[key] = val

        if uuid in seen:
            # Later definition overwrites earlier one — remove old
            tasks = [t for t in tasks if t.uuid != uuid]
        seen.add(uuid)

        tasks.append(Task(
            uuid=uuid,
            command=command,
            depends=depends,
            retry=retry,
            annotations=annotations,
        ))

    return tasks


def parse_jsonl(text: str) -> list[Task]:
    """JSONL テキストをパースし Task リストを返す。

    各行を json.loads でパースし、不正行（空行・不正 JSON・必須フィールド欠落）はスキップする。
    同一 uuid が複数回出現した場合、後の定義が優先する（parse_exec_md と同じ挙動）。
    """
    tasks: list[Task] = []
    seen: set[str] = set()

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Skipping invalid JSON line: %s", line)
            continue

        if "uuid" not in data:
            logger.warning("Skipping line missing 'uuid': %s", line)
            continue
        if "command" not in data:
            logger.warning("Skipping line missing 'command': %s", line)
            continue

        uuid = data["uuid"]
        if uuid in seen:
            tasks = [t for t in tasks if t.uuid != uuid]
        seen.add(uuid)

        tasks.append(Task(
            uuid=uuid,
            command=data["command"],
            depends=data.get("depends", []),
            retry=data.get("retry", 0),
            annotations=data.get("annotations", {}),
            result_path=data.get("result_path"),
            idempotency_key=data.get("idempotency_key"),
            engine=data.get("engine"),
            model=data.get("model"),
        ))

    return tasks


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

    # Orphan dependency check: dep not in exec.md and not in done
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
