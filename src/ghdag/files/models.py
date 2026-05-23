from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class PathTraversalError(ValueError):
    """Raised when a file path escapes the repository root."""


@dataclass(frozen=True)
class MdFile:
    path: str
    frontmatter: dict[str, Any]
    content: str


class AppendStatus(Enum):
    APPENDED = "appended"
    NOOP = "noop"
    RECOVERED = "recovered"


@dataclass(frozen=True)
class AppendResult:
    status: AppendStatus
    path: str
    section: str
    body_hash: str


@dataclass(frozen=True)
class WriteResult:
    path: str
    bytes_written: int


class PromoteStatus(Enum):
    PROMOTED = "promoted"
    NOOP = "noop"


@dataclass(frozen=True)
class PromoteResult:
    status: PromoteStatus
    source_path: str
    target_path: str
    section: str
