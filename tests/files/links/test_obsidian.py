"""Tests for ghdag.files.links.obsidian (Issue #759 AC1–AC6b)."""
from __future__ import annotations

from ghdag.files.links.obsidian import rewrite_links, summary_footer

TS = '20260509120000'
UUID = 'abc-def-123'

class TestSummaryFooter:

    def test_summary_footer_includes_slack_done_link(self) -> None:
        out = summary_footer(TS, 'sum-456', ['jobs/20260509-cursor-result-abc.md', 'jobs/20260509-cursor-result-def.md'], slack_uuid='slack-789')
        assert '[[jobs/done/sum-456]]' in out
        assert '[[jobs/20260509-cursor-result-abc.md]]' in out
        assert '[[jobs/20260509-cursor-result-def.md]]' in out
        assert '[[jobs/done/slack-789]]' in out

    def test_summary_footer_omits_slack_when_not_given(self) -> None:
        out = summary_footer(TS, 'sum-456', ['jobs/20260509-cursor-result-abc.md'])
        assert '[[jobs/done/slack-789]]' not in out

class TestRewriteLinks:

    def test_rewrite_links_preserves_display_alias(self) -> None:
        content = '[[jobs/old.md|Result]]'
        path_map = {'jobs/old.md': 'jobs/archive/2026-05/old.md'}
        assert rewrite_links(content, path_map) == '[[jobs/archive/2026-05/old.md|Result]]'

    def test_rewrite_links_leaves_unmapped_paths_unchanged(self) -> None:
        content = '[[jobs/other.md]]'
        path_map = {'jobs/moved.md': 'jobs/archive/2026-05/moved.md'}
        assert rewrite_links(content, path_map) == '[[jobs/other.md]]'

    def test_rewrite_links_noop_on_empty_path_map(self) -> None:
        assert rewrite_links('[[jobs/a.md]]', {}) == '[[jobs/a.md]]'

    def test_rewrite_links_leaves_plain_text_unchanged(self) -> None:
        content = 'text without links'
        path_map = {'jobs/a.md': 'jobs/archive/2026-05/a.md'}
        assert rewrite_links(content, path_map) == content
