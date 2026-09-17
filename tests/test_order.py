"""Tests for TemplateOrderBuilder — TC-4, TC-5, TC-6 (Issue #396)."""

from __future__ import annotations

import pytest

from ghdag.pipeline.order import TemplateOrderBuilder


class TestBuildOrderExceptionMessages:
    def test_tc4_value_error_includes_template_path(self, tmp_path):
        """TC-4: referencing a missing context key → ValueError includes path"""
        template = tmp_path / "brushup.md"
        template.write_text("Hello ${undefined_var}", encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            builder.build_order("brushup", {})

        msg = str(exc_info.value)
        # Japanese text intentionally kept for CJK processing test
        assert "テンプレート展開エラー" in msg
        assert "brushup.md" in msg
        assert "undefined_var" in msg

    def test_tc5_value_error_includes_template_path(self, tmp_path):
        """TC-5: invalid $ syntax → ValueError includes path"""
        template = tmp_path / "invalid.md"
        template.write_text("Bad syntax ${}", encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            builder.build_order("invalid", {})

        msg = str(exc_info.value)
        # Japanese text intentionally kept for CJK processing test
        assert "テンプレート展開エラー" in msg
        assert "invalid.md" in msg

    def test_tc6_file_not_found_existing_behavior(self, tmp_path):
        """TC-6: missing template → FileNotFoundError (existing behavior)"""
        builder = TemplateOrderBuilder(tmp_path)
        with pytest.raises(FileNotFoundError) as exc_info:
            builder.build_order("nonexistent", {})

        assert "nonexistent" in str(exc_info.value)

    def test_successful_substitution_returns_content(self, tmp_path):
        """Happy path: template expansion succeeds"""
        template = tmp_path / "brushup.md"
        template.write_text("Issue: ${issue_number}", encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        result = builder.build_order("brushup", {"issue_number": "42"})
        assert result == "Issue: 42"

    def test_exception_chaining_preserved(self, tmp_path):
        """ValueError exception chaining (__cause__) is preserved"""
        template = tmp_path / "test.md"
        template.write_text("${}", encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            builder.build_order("test", {})

        assert exc_info.value.__cause__ is not None

    def test_t1_multiple_missing_vars_all_listed(self, tmp_path):
        """T1: when multiple vars are missing, all appear in the ValueError message"""
        template = tmp_path / "multi.md"
        template.write_text("${a} ${b} ${c}", encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            builder.build_order("multi", {"a": "1"})

        msg = str(exc_info.value)
        assert "b" in msg
        assert "c" in msg
        assert "a" in msg  # present in available keys

    def test_t2_empty_context_shows_empty_available_keys(self, tmp_path):
        """T2: empty context shows available keys as an empty list"""
        template = tmp_path / "empty.md"
        template.write_text("${x}", encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            builder.build_order("empty", {})

        msg = str(exc_info.value)
        assert "x" in msg
        assert "[]" in msg  # available keys empty list

    def test_t7_all_vars_missing_all_listed(self, tmp_path):
        """T7: when all vars are missing, all are listed"""
        template = tmp_path / "all_missing.md"
        template.write_text("${a} ${b}", encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            builder.build_order("all_missing", {})

        msg = str(exc_info.value)
        assert "a" in msg
        assert "b" in msg

    def test_value_error_message_includes_missing_and_available_keys(self, tmp_path):
        """ValueError message includes undefined vars and available keys (AC-1, AC-2)"""
        template = tmp_path / "test.md"
        template.write_text("${name} ${age}", encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            builder.build_order("test", {"name": "Alice"})

        msg = str(exc_info.value)
        # Japanese text intentionally kept for CJK processing test
        assert "未定義変数: ['age']" in msg
        assert "利用可能なキー: ['name']" in msg
