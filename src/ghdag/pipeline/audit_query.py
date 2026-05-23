"""pipeline/audit_query.py — audit.jsonl read-only API (Issue #983 A1-1, Issue #1046)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def _list_audit_files(audit_path: Path) -> list[Path]:
    """rotated ファイル（名前順＝時系列順）+ current を返す。"""
    directory = audit_path.parent
    rotated = sorted(directory.glob("audit.*.jsonl"))
    result = list(rotated)
    if audit_path.exists():
        result.append(audit_path)
    return result


def read_task_exit_events(
    audit_path: Path,
    *,
    uuid: str | None = None,
    correlation_id: str | None = None,
    event_type: str | None = None,
    since: float | None = None,
    limit: int | None = None,
) -> list[dict]:
    """audit.jsonl から task_exit 系イベントをフィルタして返す。

    rotated ファイル（audit.*.jsonl）+ current（audit.jsonl）を時系列順に結合して読む。
    フィルタは AND 条件。since は ISO 8601 timestamp を epoch 比較する。
    limit は結果リストの末尾（最新側）から切り出す。
    ファイルが存在しない場合は空リストを返す。JSON パース失敗行はスキップする。
    """
    files = _list_audit_files(Path(audit_path))
    if not files:
        return []

    results = []
    for path in files:
        with open(path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    rec = json.loads(stripped)
                except json.JSONDecodeError:
                    continue

                if uuid is not None and rec.get("uuid") != uuid:
                    continue
                if correlation_id is not None and rec.get("correlation_id") != correlation_id:
                    continue
                if event_type is not None and rec.get("event_type") != event_type:
                    continue
                if since is not None:
                    ts_str = rec.get("timestamp")
                    if ts_str is None:
                        continue
                    try:
                        ts_epoch = datetime.fromisoformat(ts_str).timestamp()
                    except ValueError:
                        continue
                    if ts_epoch < since:
                        continue

                results.append(rec)

    if limit is not None:
        results = results[-limit:]

    return results


def get_latest_status(audit_path: Path, correlation_id: str) -> str | None:
    """correlation_id に対応する最新 status を返す。None は未記録。"""
    events = read_task_exit_events(audit_path, correlation_id=correlation_id)
    if not events:
        return None
    return events[-1].get("status")
