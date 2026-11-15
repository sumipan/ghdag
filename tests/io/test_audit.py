"""Tests for ghdag.io.audit — audit I/O consolidation (nexus Issue #2673)."""

from __future__ import annotations


class TestCompatImports:
    """Canonical audit helpers resolve from ghdag.io.audit."""

    def test_pipeline_audit_context(self) -> None:
        from ghdag.io.audit import AuditContext

        ctx = AuditContext(source="issuesmith", correlation_id="c1")
        assert ctx.source == "issuesmith"
        assert ctx.correlation_id == "c1"
