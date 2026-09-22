"""Tests for TemplateOrderBuilder.get_template_hash (AC-12) — Issue #3431."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ghdag.pipeline.order import TemplateOrderBuilder


class TestTemplateHash:
    def test_get_template_hash_returns_sha256(self, tmp_path):
        """AC-12: get_template_hash returns SHA-256 hex digest of template content."""
        template = tmp_path / "p1.md"
        content = "Hello ${issue_number}"
        template.write_text(content, encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        result = builder.get_template_hash("p1")

        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert result == expected

    def test_hash_changes_when_content_changes(self, tmp_path):
        """AC-12: modifying template content changes the hash."""
        template = tmp_path / "step1.md"
        template.write_text("original content", encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        hash1 = builder.get_template_hash("step1")

        template.write_text("modified content", encoding="utf-8")
        hash2 = builder.get_template_hash("step1")

        assert hash1 != hash2

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

    def test_hash_is_64_char_hex_string(self, tmp_path):
        """AC-12: returned value is a lowercase hex string of length 64."""
        template = tmp_path / "test.md"
        template.write_text("test", encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        result = builder.get_template_hash("test")

        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_identical_content_same_hash(self, tmp_path):
        """AC-12: two templates with identical content produce identical hashes."""
        content = "same content for both"
        (tmp_path / "a.md").write_text(content, encoding="utf-8")
        (tmp_path / "b.md").write_text(content, encoding="utf-8")

        builder = TemplateOrderBuilder(tmp_path)
        assert builder.get_template_hash("a") == builder.get_template_hash("b")

    def test_hash_reads_bytes_not_text(self, tmp_path):
        """AC-12: hash is computed from raw bytes (not decoded text)."""
        content_bytes = b"Hello \xc3\xa9"  # UTF-8 for "Hello é"
        (tmp_path / "utf8.md").write_bytes(content_bytes)

        builder = TemplateOrderBuilder(tmp_path)
        result = builder.get_template_hash("utf8")

        expected = hashlib.sha256(content_bytes).hexdigest()
        assert result == expected
