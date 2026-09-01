"""ghdag pipeline result — re-export shim for ``ghdag.io.queue`` (nexus Issue #2675)."""

from __future__ import annotations

from ghdag.io.queue import QueueTask, QueueTaskStore

__all__ = ["QueueTask", "QueueTaskStore"]
