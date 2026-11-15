"""Tests for TemplateOrderBuilder.get_template_hash (AC-12) — Issue #3431."""

from __future__ import annotations

import pytest

from ghdag.pipeline.order import TemplateOrderBuilder


class TestTemplateHash:
    def test_hash_is_consistent(self, tmp_path):
        """AC-12: calling get_template_hash twice returns same result."""
        template = tmp_path / "stable.md"
        template.write_text("content", encoding="utf-8")
        builder = TemplateOrderBuilder(tmp_path)
        assert builder.get_template_hash("stable") == builder.get_template_hash("stable")

    def test_hash_raises_file_not_found(self, tmp_path):
        """AC-12: FileNotFoundError if template does not exist."""
        builder = TemplateOrderBuilder(tmp_path)
        with pytest.raises(FileNotFoundError):
            builder.get_template_hash("nonexistent")
