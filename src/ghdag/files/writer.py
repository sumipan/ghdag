from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from pathlib import Path

from ghdag.files.models import PathTraversalError, WriteResult
from ghdag.io.audit import append_audit_record, now_ts


def write_md_write_audit(
    audit_path: Path,
    *,
    path: str,
    bytes_written: int,
    source: str = "md_write",
    correlation_id: str | None = None,
    tz_name: str = "UTC",
) -> None:
    record = {
        "event": "md_write",
        "timestamp": now_ts(tz_name),
        "path": path,
        "bytes_written": bytes_written,
        "source": source,
        "correlation_id": correlation_id,
    }
    append_audit_record(audit_path, record)


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

    # Atomic replace: concurrent writers never leave a torn (concatenated) file.
    # Parent must already exist (matches prior open()-based FileNotFoundError).
    parent = resolved.parent
    fd, tmp = tempfile.mkstemp(dir=str(parent), suffix=".tmp")
    try:
        os.write(fd, encoded)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp, str(resolved))
    except BaseException:
        if fd >= 0:
            os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise

    audit_path = resolved.parent / "audit.jsonl"
    audit_kwargs: dict[str, str] = {}
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
