"""Secret redaction for audit and task event JSONL."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEFAULT_NAMED_PATTERNS: tuple[tuple[str, str], ...] = (
    ("xox_token", r"xox[abpr]-[A-Za-z0-9-]+"),
    ("xapp_token", r"xapp-[A-Za-z0-9-]+"),
    ("ghp", r"ghp_[A-Za-z0-9]{36}"),
    ("github_pat", r"github_pat_[A-Za-z0-9_]{80,}"),
    ("google_api_key", r"AIza[0-9A-Za-z_-]{35}"),
    ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("openai_sk", r"sk-[A-Za-z0-9]{20,}"),
    ("aws_access_key", r"AKIA[0-9A-Z]{16}"),
    ("x_access_token_url", r"x-access-token:[^@\s]+@"),
)

_compiled: list[tuple[str, re.Pattern[str]]] | None = None


@dataclass(frozen=True)
class RedactResult:
    text: str
    count: int


def _load_patterns_from_file(path: Path) -> list[tuple[str, str]]:
    named: list[tuple[str, str]] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\t" in stripped:
            name, pattern = stripped.split("\t", 1)
            name = name.strip() or f"pattern_{i}"
            named.append((name, pattern.strip()))
        else:
            named.append((f"pattern_{i}", stripped))
    return named


def _compile_patterns(named: list[tuple[str, str]]) -> list[tuple[str, re.Pattern[str]]]:
    out: list[tuple[str, re.Pattern[str]]] = []
    for name, raw in named:
        out.append((name, re.compile(raw)))
    return out


def init_redact_patterns(patterns_path: str | Path | None) -> None:
    """Compile patterns once at process start (or when env override changes)."""
    global _compiled
    if patterns_path:
        path = Path(patterns_path).expanduser()
        named = _load_patterns_from_file(path)
        _compiled = _compile_patterns(named) if named else _compile_patterns(
            list(_DEFAULT_NAMED_PATTERNS)
        )
    else:
        _compiled = _compile_patterns(list(_DEFAULT_NAMED_PATTERNS))


def _patterns() -> list[tuple[str, re.Pattern[str]]]:
    global _compiled
    if _compiled is None:
        init_redact_patterns(None)
    return _compiled


def redact_text(text: str) -> RedactResult:
    """Replace secret substrings with ``[REDACTED:<name>]``. Identity when unchanged."""
    total = 0
    current = text
    for name, pattern in _patterns():
        def _repl(match: re.Match[str], _name: str = name) -> str:
            nonlocal total
            if "[REDACTED:" in match.group(0):
                # Already masked by an earlier pattern; keep the more specific name.
                return match.group(0)
            total += 1
            return f"[REDACTED:{_name}]"

        new = pattern.sub(_repl, current)
        current = new
    if total == 0:
        return RedactResult(text=text, count=0)
    return RedactResult(text=current, count=total)


def redact_json_value(value: Any) -> tuple[Any, int]:
    """Recursively redact string leaves in JSON-compatible structures."""
    if isinstance(value, str):
        result = redact_text(value)
        return result.text, result.count
    if isinstance(value, dict):
        total = 0
        out: dict[Any, Any] = {}
        for key, item in value.items():
            redacted, count = redact_json_value(item)
            out[key] = redacted
            total += count
        return out, total
    if isinstance(value, list):
        total = 0
        out_list: list[Any] = []
        for item in value:
            redacted, count = redact_json_value(item)
            out_list.append(redacted)
            total += count
        return out_list, total
    return value, 0
