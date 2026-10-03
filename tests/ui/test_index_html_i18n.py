"""Static checks for ghdag/ui/static/index.html localisation (read-only)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ghdag.config.language import EN, UI_KEYS

INDEX_HTML = (
    Path(__file__).resolve().parents[2] / "src" / "ghdag" / "ui" / "static" / "index.html"
)

_CJK_RANGES = ((0x3000, 0x30FF), (0x4E00, 0x9FFF), (0xFF00, 0xFFEF))


@pytest.fixture(scope="module")
def html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _data_i18n_keys(text: str) -> set[str]:
    return set(re.findall(r'data-i18n="([^"]+)"', text))


def _js_ui_keys(text: str) -> set[str]:
    return set(re.findall(r"\bi18n\.ui\.([A-Za-z_][A-Za-z0-9_]*)", text))


def test_no_raw_cjk(html: str) -> None:
    hits = [
        (lineno, line.strip())
        for lineno, line in enumerate(html.splitlines(), 1)
        if any(lo <= ord(ch) <= hi for ch in line for lo, hi in _CJK_RANGES)
    ]
    assert hits == []


def test_referenced_keys_are_ui_keys(html: str) -> None:
    referenced = _data_i18n_keys(html) | _js_ui_keys(html)
    assert referenced, "index.html references no i18n keys"
    assert referenced - set(UI_KEYS) == set()


def test_all_ui_keys_are_used(html: str) -> None:
    referenced = _data_i18n_keys(html) | _js_ui_keys(html)
    assert set(UI_KEYS) - referenced == set()


def test_data_i18n_fallback_text_is_english(html: str) -> None:
    for key, text in re.findall(r'data-i18n="([^"]+)"[^>]*>([^<]*)<', html):
        assert text.strip() == EN.ui[key], key


def test_no_state_substring_matching(html: str) -> None:
    assert "state.includes(" not in html


def test_state_checks_use_state_id(html: str) -> None:
    for state_id in (
        "ok", "unknown", "fail", "rejected", "empty", "engine_error",
        "pending_deps", "pending_run", "running",
    ):
        assert f"'{state_id}'" in html, state_id
    assert "r.state_id" in html
