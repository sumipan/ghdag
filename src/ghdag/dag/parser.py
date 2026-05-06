"""Parse exec.md into a list of Tasks."""

from __future__ import annotations

import json
import logging
import re
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
    """
    path = Path(exec_md_path)
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

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
        ))

    return tasks
