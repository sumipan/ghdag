"""Tests for TemplateOrderBuilder — TC-4, TC-5, TC-6 (Issue #396)."""

from __future__ import annotations

import pytest

from ghdag.pipeline.order import TemplateOrderBuilder


class TestBuildOrderExceptionMessages:
    def test_tc4_key_error_includes_template_path(self, tmp_path):
        """TC-4: context にないキーを参照 → KeyError にパスが含まれる"""
        template = tmp_path / "brushup.md"
        template.write_text("Hello ${undefined_var}", encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        with pytest.raises(KeyError) as exc_info:
            builder.build_order("brushup", {})

        msg = str(exc_info.value)
        assert "テンプレート展開エラー" in msg
        assert "brushup.md" in msg
        assert "undefined_var" in msg

    def test_tc5_value_error_includes_template_path(self, tmp_path):
        """TC-5: 不正な $ 構文 → ValueError にパスが含まれる"""
        template = tmp_path / "invalid.md"
        template.write_text("Bad syntax ${}", encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            builder.build_order("invalid", {})

        msg = str(exc_info.value)
        assert "テンプレート展開エラー" in msg
        assert "invalid.md" in msg

    def test_tc6_file_not_found_existing_behavior(self, tmp_path):
        """TC-6: 存在しないテンプレート → FileNotFoundError（既存動作）"""
        builder = TemplateOrderBuilder(tmp_path)
        with pytest.raises(FileNotFoundError) as exc_info:
            builder.build_order("nonexistent", {})

        assert "nonexistent" in str(exc_info.value)

    def test_successful_substitution_returns_content(self, tmp_path):
        """正常系: テンプレート展開が成功する"""
        template = tmp_path / "brushup.md"
        template.write_text("Issue: ${issue_number}", encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        result = builder.build_order("brushup", {"issue_number": "42"})
        assert result == "Issue: 42"

    def test_exception_chaining_preserved(self, tmp_path):
        """例外チェーニング（__cause__）が保持される"""
        template = tmp_path / "test.md"
        template.write_text("${missing}", encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        with pytest.raises(KeyError) as exc_info:
            builder.build_order("test", {})

        assert exc_info.value.__cause__ is not None
