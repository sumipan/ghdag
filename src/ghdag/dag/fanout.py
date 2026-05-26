"""Fan-out specification parsing and child task record building."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import yaml

logger = logging.getLogger(__name__)

FANOUT_SEPARATOR = "--fo--"


class FanoutError(ValueError):
    """Raised when a fan-out spec contains invalid entries."""


@dataclass
class FanOutItem:
    id: str
    command: str


@dataclass
class FanOutSpec:
    children: list[FanOutItem]


def parse_fanout_spec(result_path: str | None) -> FanOutSpec | None:
    """Detect and parse a ``ghdag_fanout:`` YAML block from the tail of a result file.

    Searches backwards for the last line starting with ``ghdag_fanout:``, then
    verifies a ``---`` separator exists before it. Parses everything from the
    anchor line as YAML. Returns None if no anchor or separator is found, or
    if parsing fails (with a warning log). Raises ValueError if child ``id``
    values are duplicated.
    """
    if result_path is None:
        return None

    try:
        with open(result_path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    lines = content.splitlines()

    fanout_line_idx: int | None = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith("ghdag_fanout:"):
            fanout_line_idx = i
            break

    if fanout_line_idx is None:
        return None

    if not any(lines[i].strip() == "---" for i in range(fanout_line_idx)):
        return None

    yaml_text = "\n".join(lines[fanout_line_idx:])
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        logger.warning("parse_fanout_spec: invalid YAML in %s: %s", result_path, exc)
        return None

    if not isinstance(data, dict) or "ghdag_fanout" not in data:
        return None

    fanout_data = data["ghdag_fanout"]
    if not isinstance(fanout_data, dict):
        return None

    children_data = fanout_data.get("children") or []
    if not children_data:
        return None

    ids: list[str] = []
    children: list[FanOutItem] = []
    for c in children_data:
        try:
            child_id = c["id"]
            child_cmd = c["command"]
        except (KeyError, TypeError) as exc:
            logger.warning("parse_fanout_spec: malformed child entry in %s: %s", result_path, exc)
            return None
        if FANOUT_SEPARATOR in str(child_id):
            raise FanoutError(
                f"child id {child_id!r} contains reserved separator {FANOUT_SEPARATOR!r}"
            )
        ids.append(child_id)
        children.append(FanOutItem(id=child_id, command=child_cmd))

    if len(ids) != len(set(ids)):
        raise FanoutError(f"Duplicate child ids in fanout spec: {ids}")

    return FanOutSpec(children=children)


def build_child_exec_line(child_uuid: str, command: str) -> str:
    """Build an exec.jsonl-format line for a fan-out child task."""
    return f"{child_uuid}: {command}"


def build_child_jsonl_record(child_uuid: str, command: str) -> str:
    """Build an exec.jsonl-format JSON line for a fan-out child task."""
    return json.dumps({"uuid": child_uuid, "command": command})
