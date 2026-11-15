"""Tests for InlineOrderBuilder — AC-1 (Issue #678)."""

from __future__ import annotations

from ghdag.io.audit import AuditContext
from ghdag.pipeline.order import InlineOrderBuilder

_TEST_AUDIT_CTX = AuditContext(source="test")


class TestInlineOrderBuilder:
    def test_normal_variable_expansion(self):
        builder = InlineOrderBuilder()
        result = builder.build_order("Hello ${name}", {"name": "world"})
        assert result == "Hello world"

    def test_no_variables(self):
        builder = InlineOrderBuilder()
        result = builder.build_order("fixed text", {})
        assert result == "fixed text"

    def test_multiple_variables(self):
        builder = InlineOrderBuilder()
        result = builder.build_order("${a} and ${b}", {"a": "X", "b": "Y"})
        assert result == "X and Y"
