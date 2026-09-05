"""ghdag.io.exec_jsonl — unified exec.jsonl read/write (nexus Issue #2674)."""

from __future__ import annotations

import fcntl
import json
import logging
from collections.abc import Callable
from pathlib import Path

from ghdag.core.models.dag import Task
from ghdag.io.audit import AuditContext, write_audit_log
from ghdag.quota import QuotaGate

logger = logging.getLogger(__name__)

__all__ = [
    "read",
    "parse",
    "parse_as_dict",
    "check_idempotency",
    "append",
    "remove_by_predicate",
    "remove_by_uuids",
    "prune",
    "load_uuids",
    "validate",
    "repair",
    "extract_uuid",
]


def read(exec_jsonl_path: Path) -> str:
    """Read the entire exec.jsonl under LOCK_SH and return its text."""
    path = Path(exec_jsonl_path)
    with open(path, encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            return f.read()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def parse(text: str) -> list[Task]:
    """Parse JSONL text into a Task list (duplicate uuid → last line wins)."""
    tasks: list[Task] = []
    seen: set[str] = set()

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Skipping invalid JSON line: %s", line)
            continue

        if "uuid" not in data:
            logger.warning("Skipping line missing 'uuid': %s", line)
            continue
        if "command" not in data:
            logger.warning("Skipping line missing 'command': %s", line)
            continue

        uuid = data["uuid"]
        if uuid in seen:
            tasks = [t for t in tasks if t.uuid != uuid]
        seen.add(uuid)

        tasks.append(
            Task(
                uuid=uuid,
                command=data["command"],
                depends=data.get("depends", []),
                retry=data.get("retry", 0),
                annotations=data.get("annotations", {}),
                result_path=data.get("result_path") or None,
                idempotency_key=data.get("idempotency_key"),
                engine=data.get("engine"),
                model=data.get("model"),
                result_finalize=data.get("result_finalize"),
            )
        )

    return tasks


def parse_as_dict(exec_jsonl_path: Path) -> dict[str, str]:
    """Return {uuid: command} from exec.jsonl (PipelineState.parse_exec_tasks compat)."""
    path = Path(exec_jsonl_path)
    if not path.exists():
        return {}

    result: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            uuid = data.get("uuid")
            command = data.get("command")
            if uuid and command:
                result[uuid] = command
    return result


def check_idempotency(exec_jsonl_path: Path, key: str) -> bool:
    """Return True if no record with the given idempotency_key exists (unprocessed)."""
    path = Path(exec_jsonl_path)
    if not path.exists():
        return True
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                if rec.get("idempotency_key") == key:
                    return False
            except json.JSONDecodeError:
                continue
    return True


def append(
    exec_jsonl_path: Path,
    records: list[dict],
    audit_context: AuditContext,
    *,
    audit_path: Path,
    idempotency_key: str | None = None,
    default_permission_uuids: list[str] | None = None,
    quota_gate: QuotaGate | None = None,
) -> None:
    """Append records as JSONL under LOCK_EX and write an audit enqueue record."""
    if not records:
        return

    if audit_context.request_id:
        for rec in records:
            rec.setdefault("annotations", {})["_request_id"] = audit_context.request_id

    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    path = Path(exec_jsonl_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write("\n".join(lines) + "\n")
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    if quota_gate is not None:
        for rec in records:
            uuid = rec.get("uuid")
            engine = rec.get("engine")
            if not isinstance(uuid, str):
                continue
            role, role_engines = _quota_role_fields(rec)
            if engine is None and role is None:
                continue
            quota_gate.admit(
                task_uuid=uuid,
                engine=str(engine) if engine is not None else None,
                phase="enqueue",
                role=role,
                role_engines=role_engines,
            )

    uuids = [r["uuid"] for r in records if "uuid" in r]
    dp_uuids = default_permission_uuids
    if dp_uuids is None:
        dp_uuids = [
            r["uuid"]
            for r in records
            if r.get("annotations", {}).get("default_permission_applied")
        ]
    write_audit_log(
        Path(audit_path),
        task_uuids=uuids,
        exec_lines_count=len(records),
        context=audit_context,
        idempotency_key=idempotency_key,
        default_permission_uuids=dp_uuids or None,
    )


def remove_by_predicate(
    exec_jsonl_path: Path,
    predicate: Callable[[dict], bool],
) -> int:
    """Remove records for which predicate returns True. LOCK_EX read+rewrite."""
    path = Path(exec_jsonl_path)
    if not path.exists():
        return 0

    with open(path, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            lines = f.readlines()
            new_lines: list[str] = []
            removed = 0
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    new_lines.append(line)
                    continue
                try:
                    data = json.loads(stripped)
                    if predicate(data):
                        removed += 1
                        continue
                except json.JSONDecodeError:
                    pass
                new_lines.append(line)
            if removed > 0:
                f.seek(0)
                f.writelines(new_lines)
                f.truncate()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    return removed


def remove_by_uuids(exec_jsonl_path: Path, uuids: set[str]) -> int:
    """Remove lines whose uuid is in ``uuids``. LOCK_EX read+rewrite."""
    path = Path(exec_jsonl_path)
    if not path.exists():
        return 0

    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            lines = f.readlines()
            new_lines: list[str] = []
            removed = 0
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    new_lines.append(line)
                    continue
                try:
                    data = json.loads(stripped)
                    if data.get("uuid") in uuids:
                        removed += 1
                        continue
                except json.JSONDecodeError:
                    pass
                new_lines.append(line)
            if removed > 0:
                f.seek(0)
                f.truncate()
                f.writelines(new_lines)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return removed


def _quota_role_fields(record: dict) -> tuple[str | None, list[str] | None]:
    annotations = record.get("annotations") or {}
    role = record.get("role")
    if role is None and isinstance(annotations, dict):
        role = annotations.get("role")

    role_engines = record.get("role_engines")
    if role_engines is None and isinstance(annotations, dict):
        role_engines = annotations.get("role_engines")

    if role is not None and not isinstance(role, str):
        role = str(role)
    if isinstance(role_engines, list):
        role_engines = [str(engine) for engine in role_engines]
    else:
        role_engines = None
    return role, role_engines


def extract_uuid(line: str) -> str | None:
    """Extract a lowercased uuid from a JSONL line, or None."""
    stripped = line.strip()
    if not stripped or not stripped.startswith("{"):
        return None
    try:
        obj = json.loads(stripped)
        uuid = obj.get("uuid", "")
        return uuid.lower() or None
    except (json.JSONDecodeError, AttributeError):
        return None


def load_uuids(exec_jsonl_path: Path) -> tuple[list[str], set[str]]:
    """Return (lines with keepends, uuid set). No lock."""
    path = Path(exec_jsonl_path)
    if not path.exists():
        return [], set()
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    uuids: set[str] = set()
    for line in lines:
        uuid = extract_uuid(line)
        if uuid:
            uuids.add(uuid)
    return lines, uuids


def prune(
    exec_jsonl_path: Path,
    prune_uuids: set[str],
    *,
    dry_run: bool = False,
) -> int:
    """Remove lines whose uuid is in ``prune_uuids``. LOCK_EX when writing."""
    path = Path(exec_jsonl_path)
    if not path.exists():
        return 0

    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            lines = f.readlines()
            pruned = 0
            new_lines: list[str] = []
            for line in lines:
                uuid_in_line = extract_uuid(line)
                if uuid_in_line and uuid_in_line in prune_uuids:
                    pruned += 1
                    if dry_run:
                        print(f"[dry] prune exec entry: {line.rstrip()[:80]}")
                else:
                    new_lines.append(line)
            if pruned > 0 and not dry_run:
                f.seek(0)
                f.truncate()
                f.writelines(new_lines)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return pruned


def validate(exec_jsonl_path: Path) -> list[tuple[int, str]]:
    """Return [(1-based lineno, text), ...] for lines that fail json.loads."""
    path = Path(exec_jsonl_path)
    if not path.exists():
        raise FileNotFoundError(path)

    invalid: list[tuple[int, str]] = []
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            stripped = raw.rstrip("\n")
            if not stripped.strip():
                continue
            try:
                json.loads(stripped)
            except json.JSONDecodeError:
                invalid.append((lineno, stripped))
    return invalid


def repair(exec_jsonl_path: Path, *, dry_run: bool = False) -> int:
    """Remove lines that fail json.loads (and blank lines). LOCK_EX when writing."""
    path = Path(exec_jsonl_path)
    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            lines = f.readlines()
            keep: list[str] = []
            removed = 0
            for raw in lines:
                stripped = raw.rstrip("\n")
                if not stripped.strip():
                    removed += 1
                    continue
                try:
                    json.loads(stripped)
                    keep.append(raw)
                except json.JSONDecodeError:
                    removed += 1
            if removed > 0 and not dry_run:
                f.seek(0)
                f.truncate()
                f.writelines(keep)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    return removed
