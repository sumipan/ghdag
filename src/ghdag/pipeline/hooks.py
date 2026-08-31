"""pipeline/hooks.py — AuditHooks re-export shim."""

from __future__ import annotations

from ghdag.dag.audit_hooks import AuditHooks

__all__ = ["AuditHooks"]
