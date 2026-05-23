from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MdFile:
    path: str
    frontmatter: dict[str, Any]
    content: str
