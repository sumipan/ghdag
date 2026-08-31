"""ghdag.io._rotate — audit.jsonl size-based rotation."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
_MAX_AUDIT_BYTES = 64 * 1024 * 1024


def _do_rotate(audit_path: Path) -> None:
    ts = datetime.now(JST).strftime("%Y-%m-%dT%H-%M-%S")
    rotated = audit_path.with_name(f"audit.{ts}.jsonl")
    audit_path.rename(rotated)


def _maybe_rotate(audit_path: Path) -> None:
    if not audit_path.exists():
        return
    try:
        if audit_path.stat().st_size > _MAX_AUDIT_BYTES:
            _do_rotate(audit_path)
    except OSError as e:
        print(f"[audit] warning: rotation failed: {e}", file=sys.stderr)
