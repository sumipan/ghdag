"""MetricsRecorder: JSONL append with fcntl exclusive lock."""

from __future__ import annotations

import fcntl
import json
import traceback
import warnings
from pathlib import Path

from ghdag.io.audit import epoch_ts

from .models import TaskMetrics


class MetricsRecorder:
    def __init__(self, output_path: str | Path, *, tz_name: str = "UTC") -> None:
        self._output_path = Path(output_path)
        self._tz_name = tz_name

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
        timestamp = epoch_ts(metrics.finished_at, self._tz_name)
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
            "cost_usd": metrics.cost_usd,
            "cache_read_tokens": metrics.cache_read_tokens,
            "cache_creation_tokens": metrics.cache_creation_tokens,
        }
        if metrics.additional_tags is not None:
            record.update(metrics.additional_tags)
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(self._output_path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(line)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
