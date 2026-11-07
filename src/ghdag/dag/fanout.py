"""Fan-out specification parsing and child task record building."""

from __future__ import annotations

import json
from dataclasses import dataclass

import yaml

from ghdag.core.vocabulary import FANOUT_ANCHOR, FANOUT_KEY, FANOUT_SEPARATOR
from ghdag.exceptions import ConfigError

_FANOUT_CHILDREN_HINT = "children must be a list of {id, command} mappings"


class FanoutError(ConfigError):
    """Raised when a fan-out spec contains invalid entries."""

    code = "FANOUT_INVALID"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(
            message,
            code=code,
            hint=hint if hint is not None else _FANOUT_CHILDREN_HINT,
        )


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
    anchor line as YAML. Returns None if no anchor is found, if the file cannot
    be read, or if ``children`` is an empty list (no fan-out needed). Raises
    ``FanoutError`` when an anchor is present but the spec is invalid.
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
        if lines[i].startswith(FANOUT_ANCHOR):
            fanout_line_idx = i
            break

    if fanout_line_idx is None:
        return None

    if not any(lines[i].strip() == "---" for i in range(fanout_line_idx)):
        raise FanoutError("ghdag_fanout: anchor without preceding '---' line")

    yaml_text = "\n".join(lines[fanout_line_idx:])
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise FanoutError(f"invalid YAML after ghdag_fanout: anchor: {exc}") from exc

    if not isinstance(data, dict) or FANOUT_KEY not in data:
        raise FanoutError("ghdag_fanout must be a mapping")

    fanout_data = data[FANOUT_KEY]
    if not isinstance(fanout_data, dict):
        raise FanoutError("ghdag_fanout must be a mapping")

    if "children" not in fanout_data or fanout_data["children"] is None:
        raise FanoutError("ghdag_fanout.children is missing")

    children_data = fanout_data["children"]
    if not isinstance(children_data, list):
        raise FanoutError("ghdag_fanout.children must be a list")

    if not children_data:
        return None

    ids: list[str] = []
    children: list[FanOutItem] = []
    for i, c in enumerate(children_data):
        if not isinstance(c, dict):
            raise FanoutError(f"child #{i}: entry must be a mapping")
        if "id" not in c or not isinstance(c["id"], str):
            raise FanoutError(f"child #{i}: 'id' must be a string")
        if "command" not in c or not isinstance(c["command"], str):
            raise FanoutError(f"child #{i}: 'command' must be a string")
        child_id = c["id"]
        child_cmd = c["command"]
        if FANOUT_SEPARATOR in child_id:
            raise FanoutError(
                f"child id {child_id!r} contains reserved separator {FANOUT_SEPARATOR!r}"
            )
        ids.append(child_id)
        children.append(FanOutItem(id=child_id, command=child_cmd))

    if len(ids) != len(set(ids)):
        raise FanoutError(f"Duplicate child ids in fanout spec: {ids}")

    return FanOutSpec(children=children)


def build_child_jsonl_record(child_uuid: str, command: str) -> str:
    """Build an exec.jsonl-format JSON line for a fan-out child task."""
    return json.dumps({"uuid": child_uuid, "command": command})
