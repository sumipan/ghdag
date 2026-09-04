"""pipeline/audit.py — re-export shim for ghdag.io.audit (nexus Issue #2673).

Also hosts session-compaction audit writers (Issue #90) that are not part of
the io.audit canonical set.
"""

from __future__ import annotations

import sys
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ghdag.io.audit import (
    _MAX_AUDIT_BYTES,
    AuditContext,
    _do_rotate,
    _maybe_rotate,
    append_audit_record,
    compute_prompt_hash,
    write_audit_log,
    write_llm_audit_log,
    write_llm_inference_audit,
    write_quarantine_audit,
    write_rate_limit_audit,
    write_task_exit_audit,
    write_task_retry_audit,
)

JST = timezone(timedelta(hours=9))

__all__ = [
    "AuditContext",
    "append_audit_record",
    "compute_prompt_hash",
    "write_audit_log",
    "write_compaction_audit",
    "write_llm_audit_log",
    "write_llm_inference_audit",
    "write_quarantine_audit",
    "write_rate_limit_audit",
    "write_task_exit_audit",
    "write_task_retry_audit",
    "_MAX_AUDIT_BYTES",
    "_do_rotate",
    "_maybe_rotate",
]


def write_compaction_audit(
    audit_path: Path,
    *,
    task_uuid: str,
    status: str,
    reason: str,
    parent_session_id: str | None = None,
    compacted_session_id: str | None = None,
    summary_tokens: int | None = None,
    tokens_before: int | None = None,
    tokens_after: int | None = None,
    engine: str | None = None,
    comparison_group: str | None = None,
) -> None:
    """Append a session-compaction audit record (lineage + token delta)."""
    record = {
        "schema_version": 1,
        "event_type": "session_compaction",
        "timestamp": datetime.now(JST).isoformat(),
        "uuid": task_uuid,
        "event_id": str(uuid_mod.uuid4()),
        "status": status,
        "reason": reason,
        "parent_session_id": parent_session_id,
        "compacted_session_id": compacted_session_id,
        "summary_tokens": summary_tokens,
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "engine": engine,
        "comparison_group": comparison_group,
    }
    try:
        append_audit_record(audit_path, record)
    except OSError as e:
        print(f"[audit] warning: failed to write compaction audit: {e}", file=sys.stderr)
