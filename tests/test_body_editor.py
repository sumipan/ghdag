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
    body = "Preamble\n## Design\nfoo\n### Subheading\nbar\n## Next\nbaz\n"
    assert split_h2_sections(body) == [("## Design", "## Design\nfoo\n### Subheading\nbar\n"), ("## Next", "## Next\nbaz\n")]


def test_get_section_returns_content() -> None:
    body = "## Design\nfoo\n## Acceptance Criteria\nbar"
    assert get_section(body, "Design") == "foo\n"


def test_get_section_raises_on_duplicate_heading() -> None:
    body = "## Design\nfoo\n## Design\nbar"
    with pytest.raises(ValueError, match=r"Duplicate heading: '## Design' appears 2 times"):
        get_section(body, "Design")


def test_get_section_returns_none_when_missing() -> None:
    body = "## Background\nfoo"
    assert get_section(body, "Design") is None


def test_count_heading_counts_occurrences() -> None:
    body = "## Design\nfoo\n## Design\nbar"
    assert count_heading(body, "Design") == 2


def test_count_heading_returns_zero_for_missing_heading() -> None:
    body = "## Background\nfoo"
    assert count_heading(body, "Design") == 0


def test_upsert_section_replaces_existing_section() -> None:
    body = "## Design\nold\n## Next"
    assert upsert_section(body, "Design", "new") == "## Design\nnew\n## Next"


def test_upsert_section_appends_at_end_when_missing() -> None:
    body = "## Background\nfoo"
    assert upsert_section(body, "Design", "new") == "## Background\nfoo\n\n## Design\nnew"


def test_upsert_section_raises_when_duplicates_exist() -> None:
    body = "## Design\na\n## Design\nb\n## Next"
    with pytest.raises(ValueError, match=r"Duplicate heading: '## Design' appears 2 times"):
        upsert_section(body, "Design", "new")


def test_get_subsections_returns_matching_h4_sections() -> None:
    body = "## Design\n#### Sub#1: X\nfoo\n#### Sub#2: Y\nbar\n## Next"
    assert get_subsections(body, "Design", "#### Sub#") == [
        ("#### Sub#1: X", "foo\n"),
        ("#### Sub#2: Y", "bar\n"),
    ]


def test_get_subsections_returns_empty_for_missing_parent_heading() -> None:
    body = "## Background\nfoo"
    assert get_subsections(body, "Design", "#### Sub#") == []


def test_upsert_section_empty_body_appends() -> None:
    assert upsert_section("", "A", "new\n") == "## A\nnew"


def test_get_section_no_sections_returns_none() -> None:
    assert get_section("no sections here", "A") is None


def test_count_heading_counts_single() -> None:
    body = "## A\ncontent\n## A\ncontent2\n## B\nother\n"
    assert count_heading(body, "A") == 2
    assert count_heading(body, "B") == 1
