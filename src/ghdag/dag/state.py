"""Manage task completion state via the done directory (jobs/done/).

Re-export shim for ``ghdag.io.done`` (nexus Issue #2675).
"""

from __future__ import annotations

from ghdag.io.done import is_done, load_done_from_dir, load_succeeded_from_dir, mark_done

__all__ = ["is_done", "mark_done", "load_done_from_dir", "load_succeeded_from_dir"]
