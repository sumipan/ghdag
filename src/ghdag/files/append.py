from __future__ import annotations

import fcntl
import hashlib
import re
from pathlib import Path

from ghdag.exceptions import GhdagError
from ghdag.files.models import AppendResult, AppendStatus, PathTraversalError

_NEXT_HEADING_RE = re.compile(r"^#{1,6}\s+")


class AppendRecoverError(GhdagError, ValueError):
    """Partial write detected (start marker present without completion marker)."""


def _hash16(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _idem_marker(key: str, is_idempotency_key: bool) -> str:
    if is_idempotency_key:
        return f"<!-- ghdag:append key={key} -->"
    return f"<!-- ghdag:append sha256={key} -->"


def _start_marker(key: str, is_idempotency_key: bool) -> str:
    if is_idempotency_key:
        return f"<!-- ghdag:append:start key={key} -->"
    return f"<!-- ghdag:append:start sha256={key} -->"


def _find_section(
    lines: list[str], section: str
) -> tuple[int | None, int]:
    pattern = re.compile(r"^#{1,6}\s+" + re.escape(section) + r"\s*$")
    section_idx: int | None = None
    for i, line in enumerate(lines):
        if pattern.match(line.rstrip("\n").rstrip("\r")):
            section_idx = i
            break
    if section_idx is None:
        return None, len(lines)
    end_idx = len(lines)
    for i in range(section_idx + 1, len(lines)):
        if _NEXT_HEADING_RE.match(lines[i]):
            end_idx = i
            break
    return section_idx, end_idx


def _process_append(
    content: str,
    section: str,
    body: str,
    body_hash: str,
    idempotency_key: str | None,
) -> tuple[AppendStatus, str | None]:
    key = idempotency_key if idempotency_key is not None else body_hash
    use_key = idempotency_key is not None

    idem = _idem_marker(key, use_key)
    start = _start_marker(key, use_key)

    body_block = idem + "\n" + body
    if body and not body.endswith("\n"):
        body_block += "\n"
    elif not body:
        body_block += "\n"

    lines = content.splitlines(keepends=True)
    section_idx, section_end_idx = _find_section(lines, section)

    if section_idx is None:
        # Section not found: append new section at EOF
        prefix = "".join(lines)
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        new_content = prefix + f"## {section}\n" + body_block
        return AppendStatus.APPENDED, new_content

    section_lines = lines[section_idx + 1 : section_end_idx]
    section_text = "".join(section_lines)

    # NOOP: idempotency marker already present
    if idem in section_text:
        return AppendStatus.NOOP, None

    # RECOVERED: start marker present (with or without end marker)
    if start in section_text:
        # Find line index of start marker within section
        trim_at = section_idx + 1
        for j, sl in enumerate(section_lines):
            if start in sl:
                trim_at = section_idx + 1 + j
                break
        before = lines[:trim_at]
        after = lines[section_end_idx:]
        new_content = "".join(before) + body_block + "".join(after)
        return AppendStatus.RECOVERED, new_content

    # Normal append: insert before section_end
    before = lines[:section_end_idx]
    after = lines[section_end_idx:]
    new_content = "".join(before) + body_block + "".join(after)
    return AppendStatus.APPENDED, new_content


def md_append(
    path: str,
    section: str,
    body: str,
    *,
    idempotency_key: str | None = None,
    repo_root: Path | None = None,
    allow_recover: bool = False,
) -> AppendResult:
    root = repo_root if repo_root is not None else Path.cwd()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise PathTraversalError(f"Path traversal detected: {path}")
    if not resolved.exists():
        raise FileNotFoundError(f"File not found: {resolved}")

    body_hash = _hash16(body)

    with open(resolved, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            content = f.read()
            status, new_content = _process_append(
                content, section, body, body_hash, idempotency_key
            )
            if status == AppendStatus.RECOVERED and not allow_recover:
                key = idempotency_key if idempotency_key is not None else body_hash
                use_key = idempotency_key is not None
                start = _start_marker(key, use_key)
                raise AppendRecoverError(
                    f"Partial write detected: path={path!r}, section={section!r}, marker={start!r}"
                )
            if new_content is not None:
                f.seek(0)
                f.write(new_content)
                f.truncate()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    return AppendResult(
        status=status,
        path=path,
        section=section,
        body_hash=body_hash,
    )
