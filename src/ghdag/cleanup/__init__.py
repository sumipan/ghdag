"""ghdag cleanup — cleanup logic for the queue directory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ghdag.core.vocabulary import QUEUE_FILE_RE


@dataclass
class CleanupResult:
    archived_done: int
    archived_orphan: int
    pruned_exec: int
    swept_extras: int = 0
    detected_orphan: int = 0
    detected_dead: int = 0


def file_timestamp(path: Path) -> float:
    """Return the file timestamp. Prefers st_birthtime, falls back to st_mtime."""
    try:
        st = path.stat()
    except OSError:
        return 0.0
    return getattr(st, "st_birthtime", st.st_mtime)


from ghdag.cleanup.orchestrator import cleanup_queue  # noqa: E402

__all__ = ["cleanup_queue", "CleanupResult", "file_timestamp", "QUEUE_FILE_RE"]
