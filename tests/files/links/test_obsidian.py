"""Tests for ghdag.files.links.obsidian (Issue #759 AC1–AC6b)."""

from __future__ import annotations

from ghdag.files.links.obsidian import job_footer, rewrite_links, summary_footer

TS = "20260509120000"
UUID = "abc-def-123"


class TestJobFooter:
    def test_ac1_job_footer_links(self) -> None:
        out = job_footer(TS, UUID, "cursor")
        assert out.startswith("\n\n---\n\n## DAG（Obsidian）\n")
        assert "[[jobs/done/abc-def-123]]" in out
        assert "[[jobs/20260509120000-cursor-order-abc-def-123.md]]" in out
        assert "[[jobs/20260509120000-cursor-result-abc-def-123.md]]" in out


class TestSummaryFooter:
    def test_ac2_summary_footer_with_slack(self) -> None:
        out = summary_footer(
            TS,
            "sum-456",
            [
                "jobs/20260509-cursor-result-abc.md",
                "jobs/20260509-cursor-result-def.md",
            ],
            slack_uuid="slack-789",
        )
        assert "[[jobs/done/sum-456]]" in out
        assert "[[jobs/20260509-cursor-result-abc.md]]" in out
        assert "[[jobs/20260509-cursor-result-def.md]]" in out
        assert "[[jobs/done/slack-789]]" in out

    def test_ac2_summary_footer_without_slack(self) -> None:
        out = summary_footer(TS, "sum-456", ["jobs/20260509-cursor-result-abc.md"])
        assert "[[jobs/done/slack-789]]" not in out


class TestRewriteLinks:
    def test_ac3_path_rewrite(self) -> None:
        content = "- result: [[jobs/20260501-claude-result-abc.md]]"
        path_map = {
            "jobs/20260501-claude-result-abc.md": "jobs/archive/2026-05/20260501-claude-result-abc.md",
        }
        assert rewrite_links(content, path_map) == (
            "- result: [[jobs/archive/2026-05/20260501-claude-result-abc.md]]"
        )

    def test_ac4_display_name_preserved(self) -> None:
        content = "[[jobs/old.md|結果]]"
        path_map = {"jobs/old.md": "jobs/archive/2026-05/old.md"}
        assert rewrite_links(content, path_map) == "[[jobs/archive/2026-05/old.md|結果]]"

    def test_ac5_unmatched_unchanged(self) -> None:
        content = "[[jobs/other.md]]"
        path_map = {"jobs/moved.md": "jobs/archive/2026-05/moved.md"}
        assert rewrite_links(content, path_map) == "[[jobs/other.md]]"

    def test_ac6_multiple_links(self) -> None:
        content = "[[jobs/a.md]] と [[jobs/b.md]]"
        path_map = {
            "jobs/a.md": "jobs/archive/2026-05/a.md",
            "jobs/b.md": "jobs/archive/2026-05/b.md",
        }
        assert rewrite_links(content, path_map) == (
            "[[jobs/archive/2026-05/a.md]] と [[jobs/archive/2026-05/b.md]]"
        )

    def test_ac6a_empty_path_map(self) -> None:
        assert rewrite_links("[[jobs/a.md]]", {}) == "[[jobs/a.md]]"

    def test_ac6b_no_wikilinks(self) -> None:
        content = "リンクなしのテキスト"
        path_map = {"jobs/a.md": "jobs/archive/2026-05/a.md"}
        assert rewrite_links(content, path_map) == content
