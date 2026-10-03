"""ghdag.maintenance — queue inspection and repair API."""

from __future__ import annotations

import json
from pathlib import Path

from ghdag.io.done import is_done as _is_done
from ghdag.io.done import mark_done as _mark_done


def validate_exec_jsonl(exec_jsonl_path: Path) -> list[tuple[int, str]]:
    """Validate each line of exec.jsonl with json.loads() and return the failing lines.

    Args:
        exec_jsonl_path: path to exec.jsonl
    Returns:
        [(1-based line number, invalid line text), ...]
        Empty and whitespace-only lines are skipped and not reported.
    Raises:
        FileNotFoundError: if exec_jsonl_path does not exist
    """
    from ghdag.io import exec_jsonl

    return exec_jsonl.validate(Path(exec_jsonl_path))


def repair_exec_jsonl(exec_jsonl_path: Path, *, dry_run: bool = False) -> int:
    """Remove lines that json.loads() cannot parse from exec.jsonl.

    Args:
        exec_jsonl_path: path to exec.jsonl
        dry_run: if True, do not modify the file; only return the number of lines to remove
    Returns:
        Number of lines removed (or to be removed)
    Note:
        Takes an exclusive lock with fcntl.LOCK_EX when writing.
        Empty and whitespace-only lines are also removed.
    """
    from ghdag.io import exec_jsonl

    return exec_jsonl.repair(Path(exec_jsonl_path), dry_run=dry_run)


def repair_jobs_done(
    exec_jsonl_path: Path,
    done_dir: Path,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Restore done markers from exec.jsonl entries.

    Args:
        exec_jsonl_path: path to exec.jsonl
        done_dir: path to the done marker directory
        dry_run: if True, do not create marker files; only return the counts
    Returns:
        {"restored": int, "skipped": int}
    Note:
        result_path is resolved relative to exec_jsonl_path.parent.
        Entries whose result_path does not exist are counted as neither restored nor skipped.
        Done markers are written with ``ghdag.io.done.mark_done`` (the ops layer does not depend on dag).
    """
    base = Path(exec_jsonl_path).parent
    done_dir = Path(done_dir)

    restored = 0
    skipped = 0

    from ghdag.io import exec_jsonl

    text = exec_jsonl.read(Path(exec_jsonl_path))
    for raw in text.splitlines(keepends=True):
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            continue

        uuid = record.get("uuid")
        result_path_str = record.get("result_path")
        if not uuid or not result_path_str:
            continue

        result_path = base / result_path_str
        if not result_path.exists():
            continue

        if _is_done(done_dir, uuid):
            skipped += 1
            continue

        content = result_path.read_text(encoding="utf-8")
        if content.startswith("REJECTED:"):
            status = "REJECTED"
        elif content == "":
            status = "EMPTY_RESULT"
        else:
            status = "0"

        if not dry_run:
            _mark_done(done_dir, uuid, status)
        restored += 1

    return {"restored": restored, "skipped": skipped}
