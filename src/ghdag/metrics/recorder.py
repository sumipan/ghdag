"""MetricsRecorder: JSONL append with fcntl exclusive lock."""

from __future__ import annotations

import fcntl
import json
import traceback
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import TaskMetrics

_JST = timezone(timedelta(hours=9))


class MetricsRecorder:
    def __init__(self, output_path: str | Path) -> None:
        self._output_path = Path(output_path)

    def record(self, metrics: TaskMetrics) -> None:
        """JSONL 1行を追記（fcntl.LOCK_EX で排他ロック）。例外は内部で捕捉。"""
        try:
            self._write(metrics)
        except Exception as exc:
            warnings.warn(f"MetricsRecorder: failed to record metrics for {metrics.uuid}: {exc}")
            try:
                error_log = Path(str(self._output_path) + ".errors.log")
                with open(error_log, "a", encoding="utf-8") as f:
                    f.write(traceback.format_exc())
            except Exception:
                pass

    def _write(self, metrics: TaskMetrics) -> None:
        timestamp = datetime.fromtimestamp(metrics.finished_at, tz=_JST).isoformat()
        record = {
            "uuid": metrics.uuid,
            "engine": metrics.engine,
            "model": metrics.model,
            "wall_time_sec": metrics.wall_time_sec,
            "token_count": metrics.token_count,
            "status": metrics.status,
            "started_at": metrics.started_at,
            "finished_at": metrics.finished_at,
            "timestamp": timestamp,
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(self._output_path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(line)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
