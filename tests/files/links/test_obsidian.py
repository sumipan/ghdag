"""Tests for ghdag.files.links.obsidian (Issue #7916)."""

from __future__ import annotations

from ghdag.files.links.obsidian import rewrite_links


class TestRewriteLinks:
    def test_rewrite_links_preserves_display_alias(self) -> None:
        content = "[[jobs/old.md|Result]]"
        path_map = {"jobs/old.md": "jobs/archive/2026-05/old.md"}
        assert rewrite_links(content, path_map) == "[[jobs/archive/2026-05/old.md|Result]]"

    def test_rewrite_links_leaves_unmapped_paths_unchanged(self) -> None:
        content = "[[jobs/other.md]]"
        path_map = {"jobs/moved.md": "jobs/archive/2026-05/moved.md"}
        assert rewrite_links(content, path_map) == "[[jobs/other.md]]"

    def test_rewrite_links_noop_on_empty_path_map(self) -> None:
        assert rewrite_links("[[jobs/a.md]]", {}) == "[[jobs/a.md]]"

    def test_rewrite_links_leaves_plain_text_unchanged(self) -> None:
        content = "text without links"
        path_map = {"jobs/a.md": "jobs/archive/2026-05/a.md"}
        assert rewrite_links(content, path_map) == content
