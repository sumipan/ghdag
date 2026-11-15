"""Tests for TemplateOrderBuilder — TC-4, TC-5, TC-6 (Issue #396)."""

from __future__ import annotations

import pytest

from ghdag.pipeline.order import TemplateOrderBuilder


class TestBuildOrderExceptionMessages:

    def test_exception_chaining_preserved(self, tmp_path):
        """ValueError exception chaining (__cause__) is preserved"""
        template = tmp_path / "test.md"
        template.write_text("${}", encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            builder.build_order("test", {})

        assert exc_info.value.__cause__ is not None

    def test_value_error_message_includes_missing_and_available_keys(self, tmp_path):
        """ValueError message includes undefined vars and available keys (AC-1, AC-2)"""
        template = tmp_path / "test.md"
        template.write_text("${name} ${age}", encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            builder.build_order("test", {"name": "Alice"})

        msg = str(exc_info.value)
        assert "undefined variables: ['age']" in msg
        assert "available keys: ['name']" in msg
