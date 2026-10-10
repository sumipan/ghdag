"""Re-export of :mod:`ghdag.core.stall_guard` (kept for backward compatibility).

``read_ps`` runs ``ps`` in a subprocess, so it lives in :mod:`ghdag.llm.engines`
instead of core.
"""

from __future__ import annotations

from ghdag.core.stall_guard import (
    ProcInfo,
    StallEvent,
    StallTracker,
    find_stalled_state_readers,
    parse_ps_output,
    scan_process_tree,
)
from ghdag.llm.engines import read_ps

__all__ = [
    "ProcInfo",
    "StallEvent",
    "StallTracker",
    "find_stalled_state_readers",
    "parse_ps_output",
    "read_ps",
    "scan_process_tree",
]
