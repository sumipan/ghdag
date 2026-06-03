from __future__ import annotations

import pytest

from ghdag.markdown.body_editor import (
    count_heading,
    get_section,
    get_subsections,
    split_h2_sections,
    upsert_section,
)


def test_split_h2_sections_splits_by_h2_only() -> None:
    body = "前文\n## 設計\nfoo\n### 小見出し\nbar\n## 次\nbaz\n"
    assert split_h2_sections(body) == [("## 設計", "## 設計\nfoo\n### 小見出し\nbar\n"), ("## 次", "## 次\nbaz\n")]


def test_get_section_returns_content() -> None:
    body = "## 設計\nfoo\n## 受け入れ条件\nbar"
    assert get_section(body, "設計") == "foo\n"


def test_get_section_raises_on_duplicate_heading() -> None:
    body = "## 設計\nfoo\n## 設計\nbar"
    with pytest.raises(ValueError, match=r"Duplicate heading: '## 設計' appears 2 times"):
        get_section(body, "設計")


def test_get_section_returns_none_when_missing() -> None:
    body = "## 背景\nfoo"
    assert get_section(body, "設計") is None


def test_count_heading_counts_occurrences() -> None:
    body = "## 設計\nfoo\n## 設計\nbar"
    assert count_heading(body, "設計") == 2


def test_count_heading_returns_zero_for_missing_heading() -> None:
    body = "## 背景\nfoo"
    assert count_heading(body, "設計") == 0


def test_upsert_section_replaces_existing_section() -> None:
    body = "## 設計\nold\n## 次"
    assert upsert_section(body, "設計", "new") == "## 設計\nnew\n## 次"


def test_upsert_section_appends_at_end_when_missing() -> None:
    body = "## 背景\nfoo"
    assert upsert_section(body, "設計", "new") == "## 背景\nfoo\n\n## 設計\nnew"


def test_upsert_section_raises_when_duplicates_exist() -> None:
    body = "## 設計\na\n## 設計\nb\n## 次"
    with pytest.raises(ValueError, match=r"Duplicate heading: '## 設計' appears 2 times"):
        upsert_section(body, "設計", "new")


def test_get_subsections_returns_matching_h4_sections() -> None:
    body = "## 設計\n#### サブ#1: X\nfoo\n#### サブ#2: Y\nbar\n## 次"
    assert get_subsections(body, "設計", "#### サブ#") == [
        ("#### サブ#1: X", "foo\n"),
        ("#### サブ#2: Y", "bar\n"),
    ]


def test_get_subsections_returns_empty_for_missing_parent_heading() -> None:
    body = "## 背景\nfoo"
    assert get_subsections(body, "設計", "#### サブ#") == []


def test_upsert_section_empty_body_appends() -> None:
    assert upsert_section("", "A", "new\n") == "## A\nnew"


def test_get_section_no_sections_returns_none() -> None:
    assert get_section("no sections here", "A") is None


def test_count_heading_counts_single() -> None:
    body = "## A\ncontent\n## A\ncontent2\n## B\nother\n"
    assert count_heading(body, "A") == 2
    assert count_heading(body, "B") == 1
