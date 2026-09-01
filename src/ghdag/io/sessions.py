"""ghdag.io.sessions — task uuid ごとの session_id 永続化ストア。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path


def _sessions_dir(queue_dir: str | Path) -> Path:
    return Path(queue_dir) / ".sessions"


def save(queue_dir: str | Path, task_uuid: str, engine: str, session_id: str) -> None:
    base = _sessions_dir(queue_dir)
    base.mkdir(parents=True, exist_ok=True)
    data = {"engine": engine, "session_id": session_id}
    target = base / f"{task_uuid}.json"
    fd, tmp = tempfile.mkstemp(dir=base, suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False))
        Path(tmp).replace(target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load(queue_dir: str | Path, task_uuid: str) -> tuple[str, str] | None:
    path = _sessions_dir(queue_dir) / f"{task_uuid}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    engine = data.get("engine")
    session_id = data.get("session_id")
    if isinstance(engine, str) and isinstance(session_id, str) and engine and session_id:
        return engine, session_id
    return None
