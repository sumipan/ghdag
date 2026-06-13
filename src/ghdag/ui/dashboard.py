"""audit.jsonl aggregation logic for the ghdag Web UI dashboard."""

from __future__ import annotations

import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ghdag.pipeline.audit_query import read_task_exit_events

__all__ = [
    "aggregate_task_status",
    "aggregate_token_usage",
    "aggregate_cb_firing",
    "resolve_audit_path",
]

_TASK_EXIT_EVENT_TYPES = frozenset({
    "task_complete",
    "task_failed",
    "task_rejected",
    "task_empty_result",
})

_DEFAULT_SINCE_SEC = 86400.0
_DEFAULT_WARN_THRESHOLD = 500_000
_DEFAULT_WINDOW_MINUTES = 60


def resolve_audit_path(repo_root: Path) -> Path:
    """Return audit.jsonl path from GHDAG_AUDIT_PATH or jobs/audit.jsonl."""
    env = os.environ.get("GHDAG_AUDIT_PATH")
    if env:
        return Path(env)
    return repo_root / "jobs" / "audit.jsonl"


def _epoch_to_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone().isoformat()


def _parse_timestamp_epoch(ts_str: str | None) -> float | None:
    if ts_str is None:
        return None
    try:
        return datetime.fromisoformat(ts_str).timestamp()
    except ValueError:
        return None


def _warn_threshold(override: int | None = None) -> int:
    if override is not None:
        return override
    env_val = os.environ.get("GHDAG_TOKEN_WARN_THRESHOLD")
    if env_val is not None:
        try:
            return int(env_val)
        except ValueError:
            pass
    return _DEFAULT_WARN_THRESHOLD


def aggregate_task_status(
    audit_path: Path,
    *,
    since_sec: float = _DEFAULT_SINCE_SEC,
) -> dict:
    """指定期間内のタスク完了イベントを status / failure_class 別に集計する。"""
    now = time.time()
    since_epoch = now - since_sec
    events = read_task_exit_events(audit_path, since=since_epoch)

    by_status: dict[str, int] = defaultdict(int)
    by_failure_class: dict[str, int] = defaultdict(int)
    total = 0

    for event in events:
        if event.get("event_type") not in _TASK_EXIT_EVENT_TYPES:
            continue
        total += 1
        status = event.get("status")
        if status is not None:
            by_status[str(status)] += 1
        failure_class = event.get("failure_class")
        if failure_class is not None:
            by_failure_class[str(failure_class)] += 1

    return {
        "total": total,
        "by_status": dict(by_status),
        "by_failure_class": dict(by_failure_class),
        "period_start": _epoch_to_iso(since_epoch),
        "period_end": _epoch_to_iso(now),
    }


def aggregate_token_usage(
    audit_path: Path,
    *,
    since_sec: float = _DEFAULT_SINCE_SEC,
    warn_threshold: int | None = None,
) -> dict:
    """correlation_id 単位のトークン消費を集計する。"""
    now = time.time()
    since_epoch = now - since_sec
    events = read_task_exit_events(audit_path, since=since_epoch)
    threshold = _warn_threshold(warn_threshold)

    totals: dict[str, int] = defaultdict(int)
    task_counts: dict[str, int] = defaultdict(int)

    for event in events:
        token_count = event.get("token_count")
        if token_count is None:
            continue
        correlation_id = event.get("correlation_id")
        if not correlation_id:
            continue
        cid = str(correlation_id)
        totals[cid] += int(token_count)
        task_counts[cid] += 1

    by_correlation = [
        {
            "correlation_id": cid,
            "total_tokens": total,
            "over_threshold": total > threshold,
            "task_count": task_counts[cid],
        }
        for cid, total in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    ]

    return {
        "by_correlation": by_correlation,
        "grand_total_tokens": sum(totals.values()),
        "warn_threshold": threshold,
    }


def aggregate_cb_firing(
    audit_path: Path,
    *,
    since_sec: float = _DEFAULT_SINCE_SEC,
    window_minutes: int = _DEFAULT_WINDOW_MINUTES,
) -> dict:
    """failure_class 別の失敗頻度を時間窓で集計する。"""
    now = time.time()
    period_start = now - since_sec
    window_sec = window_minutes * 60
    events = read_task_exit_events(audit_path, since=period_start)

    failure_events = [
        event for event in events
        if event.get("failure_class") is not None
    ]

    windows: list[dict] = []
    if window_sec > 0:
        current = period_start
        while current < now:
            window_end = min(current + window_sec, now)
            windows.append({
                "start": _epoch_to_iso(current),
                "end": _epoch_to_iso(window_end),
                "failure_count": 0,
                "failure_classes": {},
            })
            current = window_end

    for event in failure_events:
        ts_epoch = _parse_timestamp_epoch(event.get("timestamp"))
        if ts_epoch is None:
            continue
        failure_class = str(event["failure_class"])
        for window in windows:
            w_start = datetime.fromisoformat(window["start"]).timestamp()
            w_end = datetime.fromisoformat(window["end"]).timestamp()
            if w_start <= ts_epoch < w_end:
                window["failure_count"] += 1
                classes = window["failure_classes"]
                classes[failure_class] = classes.get(failure_class, 0) + 1
                break

    return {
        "windows": windows,
        "total_failures": len(failure_events),
    }
