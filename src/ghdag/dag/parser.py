"""Parse exec.md into a list of Tasks."""

from __future__ import annotations

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
