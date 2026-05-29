from __future__ import annotations

import fcntl
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ghdag.files._rotate import _maybe_rotate
from ghdag.files.models import PathTraversalError, WriteResult

JST = timezone(timedelta(hours=9))


def write_md_write_audit(
    audit_path: Path,
    *,
    path: str,
    bytes_written: int,
    source: str = "md_write",
    correlation_id: str | None = None,
) -> None:
    _maybe_rotate(audit_path)
    record = {
        "event": "md_write",
        "timestamp": datetime.now(JST).isoformat(),
        "path": path,
        "bytes_written": bytes_written,
        "source": source,
        "correlation_id": correlation_id,
    }
    with open(audit_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def md_write(
    path: str,
    content: str,
    *,
    repo_root: Path | None = None,
    source: str | None = None,
    correlation_id: str | None = None,
) -> WriteResult:
    root = repo_root if repo_root is not None else Path.cwd()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise PathTraversalError(f"Path traversal detected: {path}")

    encoded = content.encode("utf-8")
    bytes_written = len(encoded)

    with open(resolved, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            f.truncate()
            f.write(content)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    audit_path = resolved.parent / "audit.jsonl"
    audit_kwargs: dict[str, object] = {}
    if source is not None:
        audit_kwargs["source"] = source
    if correlation_id is not None:
        audit_kwargs["correlation_id"] = correlation_id
    try:
        write_md_write_audit(
            audit_path,
            path=path,
            bytes_written=bytes_written,
            **audit_kwargs,
        )
    except OSError as e:
        print(f"[md_write] warning: failed to write audit log: {e}", file=sys.stderr)

    return WriteResult(path=path, bytes_written=bytes_written)
