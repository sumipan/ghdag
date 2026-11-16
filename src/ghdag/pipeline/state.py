"""
pipeline/state.py — pipeline helpers (frontmatter, status rank, idempotency key aliases)

Persistence and enqueue live in ``TaskQueue`` (``task_queue.py``).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ghdag.io import exec_jsonl

build_idempotency_key = exec_jsonl.build_idempotency_key
handler_generation_key = exec_jsonl.handler_generation_key


def status_rank(status: str, status_order: tuple[str, ...]) -> int:
    """Return the index of status in status_order, or -1 if unknown."""
    try:
        return status_order.index(status)
    except ValueError:
        return -1


def parse_frontmatter(path: str | Path) -> dict:
    """Parse the YAML frontmatter (enclosed by ---) at the top of a file and return a dict.

    Returns an empty dict if there is no frontmatter.
    """
    with open(path, encoding="utf-8") as f:
        content = f.read()

    if not content.startswith("---"):
        return {}

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}

    try:
        return yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}
