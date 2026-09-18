"""Gate: tests/ must contain zero CJK characters (Issue #3384)."""
from __future__ import annotations

import re
from pathlib import Path

_CJK_RANGES = [
    (0x3040, 0x30FF),  # Hiragana + Katakana
    (0x3400, 0x9FFF),  # CJK Unified Ideographs Extension A + Unified
    (0xAC00, 0xD7AF),  # Hangul Syllables
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
]
_CJK = re.compile(
    "[" + "".join(chr(lo) + "-" + chr(hi) for lo, hi in _CJK_RANGES) + "]"
)
_MARKER = "Japanese text intentionally kept for CJK processing test"
_SKIP_SUFFIXES = {".pyc", ".pyo", ".so", ".dylib"}
_TEXT_SUFFIXES = {".py", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".md", ".toml"}


def _is_marked(lines: list[str], idx: int) -> bool:
    for back in range(0, 6):
        j = idx - back
        if j < 0:
            break
        if _MARKER in lines[j]:
            return True
    return False


def test_no_cjk_in_tests() -> None:
    root = Path(__file__).resolve().parent
    violations: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix in _SKIP_SUFFIXES:
            continue
        if path.suffix and path.suffix not in _TEXT_SUFFIXES:
            continue
        if path.name == "test_no_cjk.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if not _CJK.search(line):
                continue
            if _is_marked(lines, i):
                continue
            rel = path.relative_to(root.parent)
            violations.append(f"{rel}:{i + 1}: {line.strip()[:120]}")
    assert not violations, (
        "Unmarked CJK in tests/ — translate to ASCII or add "
        f"`# {_MARKER}` near the data:\n" + "\n".join(violations[:80])
    )
