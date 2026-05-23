from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ghdag.files.models import MdFile

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)^---\n?", re.DOTALL | re.MULTILINE)
_WIKILINK_RE = re.compile(r"^\[\[(.+?)\]\]$")


def _resolve_path(path: str) -> str:
    m = _WIKILINK_RE.match(path)
    if m:
        return f"notes/{m.group(1)}.md"
    return path


def md_read(path: str, *, repo_root: Path | None = None) -> MdFile:
    resolved = _resolve_path(path)
    root = repo_root if repo_root is not None else Path.cwd()
    full_path = root / resolved
    if not full_path.exists():
        raise FileNotFoundError(f"File not found: {full_path}")
    raw = full_path.read_text(encoding="utf-8")
    frontmatter: dict[str, Any] = {}
    content = raw
    m = _FRONTMATTER_RE.match(raw)
    if m:
        parsed = yaml.safe_load(m.group(1))
        frontmatter = parsed if isinstance(parsed, dict) else {}
        content = raw[m.end():]
    return MdFile(path=resolved, frontmatter=frontmatter, content=content)
