"""ghdag pipeline result — queue ディレクトリのファイル走査・result 読み込み API。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ghdag.core.vocabulary import QUEUE_FILE_RE


@dataclass(frozen=True)
class QueueTask:
    """queue ディレクトリ内の1タスクを表す。UUID をキーに order/result/stderr を紐付ける。"""

    uuid: str
    timestamp: str
    engine: str
    order_path: Path | None
    result_path: Path | None
    stderr_path: Path | None
    is_done: bool


class QueueTaskStore:
    def __init__(self, queue_dir: Path, done_dir: Path) -> None:
        self._queue_dir = queue_dir
        self._done_dir = done_dir

    def _scan(self) -> dict[str, dict]:
        by_uuid: dict[str, dict] = {}
        for path in self._queue_dir.iterdir():
            if not path.is_file() or path.suffix != ".md":
                continue
            m = QUEUE_FILE_RE.match(path.name)
            if not m:
                continue
            ts, engine, kind, uuid_raw = m.groups()
            uuid = uuid_raw.lower()
            entry = by_uuid.setdefault(uuid, {"ts": ts, "engine": engine})
            entry[kind] = path.resolve()
        return by_uuid

    def _done_uuids(self) -> set[str]:
        if not self._done_dir.is_dir():
            return set()
        return {p.name.lower() for p in self._done_dir.iterdir()}

    def read_result(self, uuid: str) -> str | None:
        """UUID に対応する result ファイルのテキストを返す。存在しなければ None。"""
        path = self.get_result_path(uuid)
        if path is None:
            return None
        return path.read_text(encoding="utf-8")

    def get_result_path(self, uuid: str) -> Path | None:
        """UUID に対応する result ファイルの Path を返す。存在しなければ None。"""
        by_uuid = self._scan()
        entry = by_uuid.get(uuid.lower())
        if entry is None:
            return None
        return entry.get("result")

    def list_tasks(self) -> list[QueueTask]:
        """queue_dir 内の全ファイルを走査し、UUID ごとに QueueTask を返す。"""
        by_uuid = self._scan()
        done = self._done_uuids()
        tasks: list[QueueTask] = []
        for uuid, entry in by_uuid.items():
            tasks.append(
                QueueTask(
                    uuid=uuid,
                    timestamp=entry["ts"],
                    engine=entry["engine"],
                    order_path=entry.get("order"),
                    result_path=entry.get("result"),
                    stderr_path=entry.get("stderr"),
                    is_done=uuid in done,
                )
            )
        return tasks
