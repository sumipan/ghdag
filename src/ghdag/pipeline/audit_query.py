"""pipeline/audit_query.py — re-export shim for ghdag.io.audit_query (nexus Issue #2673)."""

from __future__ import annotations

from ghdag.io.audit_query import (
    detect_correlation_bursts,
    get_correlation_top_n,
    get_latest_status,
    read_task_exit_events,
)

__all__ = [
    "read_task_exit_events",
    "get_latest_status",
    "detect_correlation_bursts",
    "get_correlation_top_n",
]
