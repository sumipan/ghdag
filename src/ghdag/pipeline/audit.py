"""pipeline/audit.py — re-export shim for ghdag.io.audit (nexus Issue #2673)."""

from __future__ import annotations

from ghdag.io.audit import (
    _MAX_AUDIT_BYTES,
    AuditContext,
    _do_rotate,
    _maybe_rotate,
    append_audit_record,
    compute_prompt_hash,
    write_audit_log,
    write_compaction_audit,
    write_llm_audit_log,
    write_llm_inference_audit,
    write_quarantine_audit,
    write_rate_limit_audit,
    write_task_exit_audit,
    write_task_retry_audit,
)

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
