"""Fan-out specification parsing and child task record building."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import yaml

from ghdag.core.capabilities import PRESETS
from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.core.exceptions import ConfigError
from ghdag.core.reply_block import find_reply_block, parse_reply_block
from ghdag.core.vocabulary import FANOUT_ANCHOR, FANOUT_KEY, FANOUT_SEPARATOR

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


@dataclass(frozen=True)
class FanOutItem:
    id: str
    prompt: str | None = None
    command: str | None = None
    engine: str | None = None
    model: str | None = None
    depends: tuple[str, ...] = ()
    permission: str | None = None


@dataclass
class FanOutSpec:
    children: list[FanOutItem]


def _validate_child_id(child_id: str, index: int) -> None:
    if FANOUT_SEPARATOR in child_id:
        raise FanoutError(
            f"child #{index}: id {child_id!r} contains reserved separator "
            f"{FANOUT_SEPARATOR!r}"
        )


def _parse_depends_list(raw: object, index: int) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise FanoutError(f"child #{index}: depends must be a list of sibling ids")
    dep_ids: list[str] = []
    for j, dep in enumerate(raw):
        if not isinstance(dep, str) or not dep:
            raise FanoutError(
                f"child #{index}: depends[{j}] must be a non-empty string"
            )
        dep_ids.append(dep)
    return tuple(dep_ids)


def _parse_flat_child(c: dict[str, Any], index: int) -> FanOutItem:
    if "id" not in c or not isinstance(c["id"], str):
        raise FanoutError(f"child #{index}: 'id' must be a string")
    child_id = c["id"]
    _validate_child_id(child_id, index)

    has_prompt = "prompt" in c and c["prompt"] is not None
    has_command = "command" in c and c["command"] is not None
    prompt = c.get("prompt")
    command = c.get("command")

    if has_prompt and has_command:
        raise FanoutError(
            f"child #{index}: prompt and command are mutually exclusive",
            hint="provide either prompt (LLM child) or command (shell argv), not both",
        )
    if not has_prompt and not has_command:
        raise FanoutError(
            f"child #{index}: missing prompt and command",
            hint="add prompt: <order body> or command: <shell argv>",
        )

    depends = _parse_depends_list(c.get("depends"), index)
    engine = c.get("engine")
    model = c.get("model")
    permission = c.get("permission")

    if has_command:
        if engine is not None:
            raise FanoutError(
                f"child #{index}: engine is not allowed on command children",
                hint="remove engine; command children use raw shell argv",
            )
        if model is not None:
            raise FanoutError(
                f"child #{index}: model is not allowed on command children",
                hint="remove model",
            )
        if permission is not None:
            raise FanoutError(
                f"child #{index}: permission is not allowed on command children",
                hint="remove permission",
            )
        if not isinstance(command, str):
            raise FanoutError(f"child #{index}: 'command' must be a string")
        return FanOutItem(id=child_id, command=command, depends=depends)

    if not isinstance(prompt, str):
        raise FanoutError(f"child #{index}: 'prompt' must be a string")

    if engine is not None:
        if not isinstance(engine, str) or engine not in ENGINE_SPECS:
            available = ", ".join(sorted(ENGINE_SPECS))
            raise FanoutError(
                f"child #{index}: unknown engine {engine!r}",
                hint=f"available engines: {available}",
            )
        if engine == "shell":
            raise FanoutError(
                f"child #{index}: shell is not allowed for prompt children",
                hint="use command: for shell argv or pick an LLM engine",
            )

    if permission is not None:
        if not isinstance(permission, str) or permission not in PRESETS:
            available = ", ".join(sorted(PRESETS))
            raise FanoutError(
                f"child #{index}: unknown permission {permission!r}",
                hint=f"available presets: {available}",
            )

    if model is not None and not isinstance(model, str):
        raise FanoutError(f"child #{index}: 'model' must be a string")

    return FanOutItem(
        id=child_id,
        prompt=prompt,
        engine=engine if isinstance(engine, str) else None,
        model=model if isinstance(model, str) else None,
        depends=depends,
        permission=permission if isinstance(permission, str) else None,
    )


def _validate_depends_graph(children: list[FanOutItem]) -> None:
    ids = {c.id for c in children}
    for i, child in enumerate(children):
        for dep in child.depends:
            if dep == child.id:
                raise FanoutError(
                    f"child #{i}: depends must not include its own id",
                    hint=f"remove self-reference {dep!r} from depends",
                )
            if dep not in ids:
                raise FanoutError(
                    f"child #{i}: unknown depends id {dep!r}",
                    hint=f"depends must reference sibling ids; {dep!r} is not in children",
                )

    visiting: set[str] = set()
    visited: set[str] = set()
    by_id = {c.id: c for c in children}

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise FanoutError(
                "cyclic depends between fan-out children",
                hint="depends must form a DAG over sibling ids",
            )
        if node_id in visited:
            return
        visiting.add(node_id)
        for dep in by_id[node_id].depends:
            visit(dep)
        visiting.remove(node_id)
        visited.add(node_id)

    for child in children:
        visit(child.id)


def _parse_children_list(children_data: list[Any]) -> list[FanOutItem]:
    if not children_data:
        return []
    children: list[FanOutItem] = []
    ids: list[str] = []
    for i, c in enumerate(children_data):
        if not isinstance(c, dict):
            raise FanoutError(f"child #{i}: entry must be a mapping")
        item = _parse_flat_child(c, i)
        ids.append(item.id)
        children.append(item)
    if len(ids) != len(set(ids)):
        raise FanoutError(f"Duplicate child ids in fanout spec: {ids}")
    _validate_depends_graph(children)
    return children


def _fanout_from_reply_block(content: str) -> FanOutSpec | None:
    block = parse_reply_block(content)
    if block is None or block.status != "ok":
        return None
    if not block.children:
        return None
    raw_children = list(block.children)
    children = _parse_children_list(raw_children)
    if not children:
        return None
    return FanOutSpec(children=children)


def _fanout_from_legacy_yaml(content: str) -> FanOutSpec | None:
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

    children: list[FanOutItem] = []
    ids: list[str] = []
    for i, c in enumerate(children_data):
        if not isinstance(c, dict):
            raise FanoutError(f"child #{i}: entry must be a mapping")
        if "id" not in c or not isinstance(c["id"], str):
            raise FanoutError(f"child #{i}: 'id' must be a string")
        if "command" not in c or not isinstance(c["command"], str):
            raise FanoutError(f"child #{i}: 'command' must be a string")
        child_id = c["id"]
        _validate_child_id(child_id, i)
        ids.append(child_id)
        children.append(FanOutItem(id=child_id, command=c["command"]))

    if len(ids) != len(set(ids)):
        raise FanoutError(f"Duplicate child ids in fanout spec: {ids}")

    return FanOutSpec(children=children)


def parse_fanout_spec(result_path: str | None) -> FanOutSpec | None:
    """Detect fan-out children from a reply block or legacy ``ghdag_fanout:`` YAML."""
    if result_path is None:
        return None

    try:
        with open(result_path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None

    if find_reply_block(content) is not None:
        return _fanout_from_reply_block(content)

    return _fanout_from_legacy_yaml(content)


def build_child_jsonl_record(child_uuid: str, command: str) -> str:
    """Build a minimal exec.jsonl line for a legacy command-only fan-out child."""
    return json.dumps({"uuid": child_uuid, "command": command, "depends": []})


def build_child_record_json(record: dict[str, object]) -> str:
    """Serialize a full exec.jsonl record for a fan-out child."""
    return json.dumps(record)
