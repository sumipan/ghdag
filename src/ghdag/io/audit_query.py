"""ghdag.io.audit_query — audit.jsonl read-only API (nexus Issue #2673)."""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

__all__ = [
    "read_task_exit_events",
    "get_latest_status",
    "detect_correlation_bursts",
    "get_correlation_top_n",
]


def _list_audit_files(audit_path: Path) -> list[Path]:
    """Return rotated files (name order == chronological order) + current."""
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
    """Filter and return task_exit-family events from audit.jsonl.

    Reads rotated files (audit.*.jsonl) + current (audit.jsonl) concatenated in
    chronological order. Filters are ANDed. ``since`` is an ISO 8601 timestamp
    compared as epoch. ``limit`` slices from the tail (newest side) of the result
    list. Returns an empty list if no file exists. Lines that fail JSON parsing
    are skipped.
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
    """Return the latest status for correlation_id. None means not recorded."""
    events = read_task_exit_events(audit_path, correlation_id=correlation_id)
    if not events:
        return None
    return events[-1].get("status")


def _aggregate_correlation_counts(events: list[dict]) -> list[dict]:
    """Aggregate count and latest timestamp per correlation_id, sorted by count desc."""
    counts: Counter[str] = Counter()
    latest_ts: dict[str, str] = {}
    for ev in events:
        cid = ev.get("correlation_id")
        if cid is None:
            continue
        counts[cid] += 1
        ts = ev.get("timestamp")
        if ts is not None:
            latest_ts[cid] = ts

    return [
        {
            "correlation_id": cid,
            "count": count,
            "latest_timestamp": latest_ts.get(cid, ""),
        }
        for cid, count in counts.most_common()
    ]


def detect_correlation_bursts(
    audit_path: Path,
    *,
    window_sec: float = 600.0,
    threshold: int = 10,
) -> list[dict]:
    """Detect correlation_id bursts within the last window_sec seconds."""
    since = time.time() - window_sec
    events = read_task_exit_events(audit_path, since=since)
    aggregated = _aggregate_correlation_counts(events)
    return [entry for entry in aggregated if entry["count"] >= threshold]


def get_correlation_top_n(
    audit_path: Path,
    *,
    since_sec: float,
    top_n: int = 20,
) -> list[dict]:
    """Return the top_n correlation_ids within the last since_sec, by count desc."""
    since = time.time() - since_sec
    events = read_task_exit_events(audit_path, since=since)
    aggregated = _aggregate_correlation_counts(events)
    return aggregated[:top_n]
