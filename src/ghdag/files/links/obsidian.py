"""Obsidian wiki-link helpers for DAG artifact edges (pure functions, no I/O)."""

from __future__ import annotations

import re

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]")


def rewrite_links(content: str, path_map: dict[str, str]) -> str:
    """Rewrite wiki-link paths using path_map; unmatched links are left unchanged."""
    if not path_map:
        return content

    def repl(match: re.Match[str]) -> str:
        path_part = match.group(1)
        display = match.group(2) or ""
        new_path = path_map.get(path_part)
        if new_path is None:
            return match.group(0)
        return f"[[{new_path}{display}]]"

    return _WIKILINK_RE.sub(repl, content)
