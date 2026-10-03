"""Gate: src/ and tests/ must contain zero raw CJK characters (Issue #3384, nexus #4468).

The ranges are written as ``\\u`` escapes so this file itself stays CJK-free.
Escape sequences and ``chr()`` calls are not the characters themselves, so they pass.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

_CJK = re.compile(
    "[%s-%s%s-%s%s-%s]" % tuple(map(chr, (0x3000, 0x30FF, 0x4E00, 0x9FFF, 0xFF00, 0xFFEF)))
)
_TEXT_SUFFIXES = {
    ".py", ".html", ".js", ".css", ".json", ".jsonl", ".md", ".txt",
    ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh", ".j2", ".tmpl",
}
_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCAN_DIRS = ("src", "tests")

# Files owned by nexus #4522 (translated / escaped there). Remove once it lands.
_PENDING = {
    "src/ghdag/llm/adapters/failure_classification.py",
    "tests/fixtures/claude_json_interactive_prompt_fullwidth.json",
    "tests/fixtures/codex_jsonl_interactive_prompt_fullwidth.jsonl",
    "tests/fixtures/cursor_stream_interactive_prompt_fullwidth.jsonl",
}


def find_cjk(root: Path) -> list[tuple[str, int]]:
    """Return ``(relative_path, line_number)`` for every line under *root* with raw CJK."""
    hits: list[tuple[str, int]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
            continue
        if _SKIP_DIRS.intersection(path.relative_to(root).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(root).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _CJK.search(line):
                hits.append((rel, lineno))
    return hits


def test_no_cjk_in_src_and_tests() -> None:
    violations: list[str] = []
    for name in _SCAN_DIRS:
        for rel, lineno in find_cjk(_REPO_ROOT / name):
            full = f"{name}/{rel}"
            if full in _PENDING:
                continue
            violations.append(f"{full}:{lineno}")
    assert not violations, (
        "Raw CJK found — translate to English or use \\u escapes:\n"
        + "\n".join(violations[:80])
    )


def test_find_cjk_detects_inserted_character(tmp_path: Path) -> None:
    copy = tmp_path / "tests"
    shutil.copytree(
        _REPO_ROOT / "tests" / "workflow",
        copy / "workflow",
        ignore=shutil.ignore_patterns(*_SKIP_DIRS),
    )
    assert find_cjk(copy) == []

    target = copy / "workflow" / "sample.py"
    target.write_text("x = 1\n# " + chr(0x3042) + "\n", encoding="utf-8")
    assert find_cjk(copy) == [("workflow/sample.py", 2)]


def test_find_cjk_detects_each_range(tmp_path: Path) -> None:
    for ch in (0x3000, 0x30FF, 0x4E00, 0x9FFF, 0xFF00, 0xFFEF):
        (tmp_path / f"u{ch:04x}.md").write_text(chr(ch), encoding="utf-8")
    (tmp_path / "escaped.py").write_text('s = "' + "\\" + 'u3042"\n', encoding="utf-8")
    assert sorted(rel for rel, _ in find_cjk(tmp_path)) == [
        "u3000.md", "u30ff.md", "u4e00.md", "u9fff.md", "uff00.md", "uffef.md",
    ]
