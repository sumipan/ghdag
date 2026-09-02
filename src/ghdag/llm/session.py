"""ghdag.llm.session — engine-independent session store."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class SessionRecord:
    """Resolved session metadata."""

    engine: str
    session_id: str
    created_at: datetime


class SessionStore:
    """File-based key -> session mapping."""

    def __init__(self, store_dir: str | Path) -> None:
        self._store_dir = Path(store_dir)

    def record(self, key: str, engine: str, session_id: str) -> None:
        self._store_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "engine": engine,
            "session_id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        target = self._record_path(key)
        fd, tmp = tempfile.mkstemp(dir=self._store_dir, suffix=".tmp")
        try:
            with open(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False))
            Path(tmp).replace(target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def lookup(self, key: str, *, max_age: timedelta | None = None) -> SessionRecord | None:
        path = self._record_path(key)
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
        if not isinstance(engine, str) or not engine:
            return None
        if not isinstance(session_id, str) or not session_id:
            return None

        created_at = self._resolve_created_at(data, path)
        if created_at is None:
            return None

        now = datetime.now(timezone.utc)
        if max_age is not None and created_at + max_age <= now:
            return None

        return SessionRecord(engine=engine, session_id=session_id, created_at=created_at)

    def invalidate(self, key: str) -> bool:
        path = self._record_path(key)
        if not path.exists():
            return False
        path.unlink()
        return True

    def gc(self, *, max_age: timedelta) -> int:
        now = datetime.now(timezone.utc)
        deleted = 0
        if not self._store_dir.exists():
            return 0
        for path in self._store_dir.glob("*.json"):
            record = self.lookup(path.stem)
            if record is None:
                continue
            if record.created_at + max_age <= now:
                path.unlink(missing_ok=True)
                deleted += 1
        return deleted

    def _record_path(self, key: str) -> Path:
        return self._store_dir / f"{key}.json"

    def _resolve_created_at(self, payload: dict[str, object], path: Path) -> datetime | None:
        raw_created_at = payload.get("created_at")
        if isinstance(raw_created_at, str):
            try:
                parsed = datetime.fromisoformat(raw_created_at)
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        return datetime.fromtimestamp(mtime, tz=timezone.utc)
