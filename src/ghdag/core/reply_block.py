"""Parse engine reply blocks from fenced YAML (info string REPLY_BLOCK_INFO) in result bodies."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import yaml

from ghdag.core.exceptions import ConfigError
from ghdag.core.vocabulary import REPLY_BLOCK_INFO, REPLY_STATUS_VALUES

_FENCE_LINE_RE = re.compile(r"^```(\S*)?\s*$")
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_ALLOWED_KEYS = frozenset({"status", "reason", "code", "engines", "after", "children"})


class ReplyBlockError(ConfigError):
    """Invalid reply-block YAML or field combination inside an engine result fence."""

    code = "REPLY_BLOCK_INVALID"

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message, code=self.code, hint=hint)


@dataclass(frozen=True)
class ReplyBlock:
    status: str
    reason: str | None = None
    code: str | None = None
    engines: tuple[str, ...] = ()
    after: str | None = None
    children: tuple[dict[str, Any], ...] = ()


def find_reply_block(text: str) -> str | None:
    """Return YAML body of the last top-level REPLY_BLOCK_INFO fence, or None."""
    lines = text.splitlines()
    generic_depth = 0
    in_ghdag = False
    body_lines: list[str] = []
    last_body: str | None = None

    for line in lines:
        m = _FENCE_LINE_RE.match(line)
        if m is None:
            if in_ghdag:
                body_lines.append(line)
            continue

        info = m.group(1) or ""
        if in_ghdag:
            in_ghdag = False
            last_body = "\n".join(body_lines)
            body_lines = []
            continue

        if generic_depth > 0:
            if info == "":
                generic_depth -= 1
            continue

        if info == REPLY_BLOCK_INFO:
            in_ghdag = True
            body_lines = []
        else:
            generic_depth += 1

    return last_body


def parse_reply_block(text: str) -> ReplyBlock | None:
    """Parse the last top-level reply block; None if absent."""
    body = find_reply_block(text)
    if body is None:
        return None
    return _parse_reply_body(body)


def _parse_reply_body(body: str) -> ReplyBlock:
    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        raise ReplyBlockError(f"invalid YAML in reply block: {exc}") from exc

    if not isinstance(data, dict):
        raise ReplyBlockError(
            "reply block must be a YAML mapping",
            hint="use keys such as status, reason, code, engines, after, children",
        )

    unknown = sorted(set(data) - _ALLOWED_KEYS)
    if unknown:
        raise ReplyBlockError(
            f"unknown key(s) in reply block: {', '.join(unknown)}",
            hint=f"remove {unknown[0]}; allowed keys are: {', '.join(sorted(_ALLOWED_KEYS))}",
        )

    if "status" not in data:
        raise ReplyBlockError(
            "missing required key: status",
            hint=(
                f"add status: one of {', '.join(REPLY_STATUS_VALUES)} "
                "(example: status: ok)"
            ),
        )

    status = data["status"]
    if not isinstance(status, str) or status not in REPLY_STATUS_VALUES:
        valid = ", ".join(REPLY_STATUS_VALUES)
        raise ReplyBlockError(
            f"invalid status: {status!r}",
            hint=f"use one of: {valid} (example: status: wait_quota)",
        )

    reason = _optional_str(data.get("reason"))
    code = _optional_str(data.get("code"))
    after = _optional_str(data.get("after"))
    engines = _parse_engines(data.get("engines"))
    children = _parse_children(data.get("children"))

    if status == "ok":
        if reason is not None:
            raise ReplyBlockError("status ok must not include reason", hint="remove reason")
        if code is not None:
            raise ReplyBlockError("status ok must not include code", hint="remove code")
        if engines:
            raise ReplyBlockError("status ok must not include engines", hint="remove engines")
        if after is not None:
            raise ReplyBlockError("status ok must not include after", hint="remove after")
    elif status == "rejected":
        if not reason:
            raise ReplyBlockError(
                "status rejected requires non-empty reason",
                hint="add reason: <short explanation>",
            )
        if code is not None:
            raise ReplyBlockError("status rejected must not include code", hint="remove code")
        if engines:
            raise ReplyBlockError("status rejected must not include engines", hint="remove engines")
        if after is not None:
            raise ReplyBlockError("status rejected must not include after", hint="remove after")
        if children:
            raise ReplyBlockError("status rejected must not include children", hint="remove children")
    elif status == "wait_quota":
        if not engines:
            raise ReplyBlockError(
                "status wait_quota requires non-empty engines",
                hint="add engines: [claude] (or other known engine names)",
            )
        if reason is not None:
            raise ReplyBlockError("status wait_quota must not include reason", hint="remove reason")
        if code is not None:
            raise ReplyBlockError("status wait_quota must not include code", hint="remove code")
        if children:
            raise ReplyBlockError("status wait_quota must not include children", hint="remove children")
        if after is not None:
            _validate_after_tz(after)
    else:  # failed
        if not code or not _CODE_RE.match(code):
            raise ReplyBlockError(
                "status failed requires code matching SCREAMING_SNAKE",
                hint="add code: SCHEMA (uppercase letters, digits, underscores)",
            )
        if engines:
            raise ReplyBlockError("status failed must not include engines", hint="remove engines")
        if after is not None:
            raise ReplyBlockError("status failed must not include after", hint="remove after")
        if children:
            raise ReplyBlockError("status failed must not include children", hint="remove children")

    return ReplyBlock(
        status=status,
        reason=reason,
        code=code,
        engines=engines,
        after=after,
        children=children,
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReplyBlockError(f"expected string, got {type(value).__name__}")
    return value


def _parse_engines(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ReplyBlockError("engines must be a list of engine names")
    names: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ReplyBlockError("engines entries must be non-empty strings")
        name = item.strip()
        if name not in seen:
            seen.add(name)
            names.append(name)
    return tuple(names)


def _parse_children(value: object) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ReplyBlockError("children must be a list of mappings")
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ReplyBlockError("each child must be a mapping")
        out.append(dict(item))
    return tuple(out)


def _validate_after_tz(after: str) -> None:
    try:
        parsed = datetime.fromisoformat(after.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReplyBlockError(f"invalid after datetime: {after}") from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ReplyBlockError(
            "after must be timezone-aware ISO 8601",
            hint="use after: 2026-01-01T00:00:00+00:00",
        )
