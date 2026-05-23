from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ghdag.files.append import md_append
from ghdag.files.models import AppendStatus, PromoteResult, PromoteStatus
from ghdag.files.reader import md_read
from ghdag.pipeline.audit import _maybe_rotate

JST = timezone(timedelta(hours=9))


def _write_promote_audit(
    audit_path: Path,
    *,
    source_path: str,
    target_path: str,
    section: str,
    status: str,
    correlation_id: str | None = None,
) -> None:
    _maybe_rotate(audit_path)
    record = {
        "event": "md_promote",
        "timestamp": datetime.now(JST).isoformat(),
        "source_path": source_path,
        "target_path": target_path,
        "section": section,
        "status": status,
        "source": "md_promote",
        "correlation_id": correlation_id,
    }
    with open(audit_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def md_promote(
    source_path: str,
    target_path: str,
    *,
    section: str = "Promoted",
    idempotency_key: str | None = None,
    repo_root: Path | None = None,
) -> PromoteResult:
    root = repo_root if repo_root is not None else Path.cwd()

    # Validate source path (md_read raises FileNotFoundError / ValueError)
    resolved_source = (root / source_path).resolve()
    if not resolved_source.is_relative_to(root.resolve()):
        raise ValueError(f"Path traversal detected: {source_path}")

    # Validate target path before calling md_append
    resolved_target = (root / target_path).resolve()
    if not resolved_target.is_relative_to(root.resolve()):
        raise ValueError(f"Path traversal detected: {target_path}")

    source_file = md_read(source_path, repo_root=root)

    key = idempotency_key if idempotency_key is not None else f"promote:{source_path}"

    append_result = md_append(
        target_path,
        section,
        source_file.content,
        idempotency_key=key,
        repo_root=root,
    )

    if append_result.status == AppendStatus.NOOP:
        promote_status = PromoteStatus.NOOP
    else:
        promote_status = PromoteStatus.PROMOTED

    audit_path = resolved_target.parent / "audit.jsonl"
    try:
        _write_promote_audit(
            audit_path,
            source_path=source_path,
            target_path=target_path,
            section=section,
            status=promote_status.value,
        )
    except OSError as e:
        print(f"[md_promote] warning: failed to write audit log: {e}", file=sys.stderr)

    return PromoteResult(
        status=promote_status,
        source_path=source_path,
        target_path=target_path,
        section=section,
    )
