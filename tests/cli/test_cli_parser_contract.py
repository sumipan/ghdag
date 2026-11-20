"""CLI parser tree contract (Issue #7547)."""

from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
from typing import Any

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cli_parser_contract.json"


def _json_default(value: Any) -> Any:
    if value is argparse.SUPPRESS:
        return {"__argparse__": "SUPPRESS"}
    if isinstance(value, type):
        return {"__type__": value.__name__}
    if callable(value) and not isinstance(value, type):
        return {"__callable__": getattr(value, "__name__", repr(value))}
    if isinstance(value, (list, tuple)):
        return [_json_default(v) for v in value]
    return value


def _type_label(action: argparse.Action) -> str | None:
    action_type = getattr(action, "type", None)
    if action_type is None or action_type is str:
        return None
    if isinstance(action_type, type):
        return action_type.__name__
    return getattr(action_type, "__name__", repr(action_type))


def _normalize_action(action: argparse.Action) -> dict[str, Any] | None:
    if isinstance(action, (argparse._SubParsersAction, argparse._HelpAction)):
        return None
    if type(action).__name__ == "_VersionAction":
        return None
    entry: dict[str, Any] = {
        "class": type(action).__name__,
        "option_strings": list(action.option_strings),
        "dest": action.dest,
        "required": action.required,
        "nargs": action.nargs,
        "default": _json_default(action.default),
        "metavar": action.metavar if isinstance(action.metavar, (str, type(None))) else repr(action.metavar),
        "help": action.help,
    }
    type_label = _type_label(action)
    if type_label is not None:
        entry["type"] = type_label
    if action.choices is not None:
        entry["choices"] = sorted(action.choices)
    const = getattr(action, "const", argparse.SUPPRESS)
    if const is not argparse.SUPPRESS:
        entry["const"] = _json_default(const)
    return entry


def _handler_label(parser: argparse.ArgumentParser) -> dict[str, str] | None:
    func = parser.get_default("func")
    if func is None:
        return None
    return {"module": func.__module__, "qualname": func.__qualname__}


def _iter_command_parsers(
    parser: argparse.ArgumentParser,
    path: tuple[str, ...] = (),
) -> list[tuple[tuple[str, ...], argparse.ArgumentParser]]:
    found: list[tuple[tuple[str, ...], argparse.ArgumentParser]] = [(path, parser)]
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, subparser in action.choices.items():
            sub_path = path + (name,)
            found.extend(_iter_command_parsers(subparser, sub_path))
    return found


def normalize_parser_tree(parser: argparse.ArgumentParser) -> dict[str, Any]:
    """Return a JSON-safe snapshot of the full CLI parser hierarchy."""
    commands: list[dict[str, Any]] = []
    for path, node in _iter_command_parsers(parser):
        actions = [
            normalized
            for action in node._actions
            if (normalized := _normalize_action(action)) is not None
        ]
        actions.sort(key=lambda item: (item["dest"], tuple(item["option_strings"])))
        commands.append(
            {
                "path": list(path),
                "prog": node.prog,
                "description": node.description,
                "epilog": node.epilog,
                "formatter_class": (
                    None
                    if node.formatter_class is argparse.HelpFormatter
                    else getattr(node.formatter_class, "__name__", repr(node.formatter_class))
                ),
                "allow_abbrev": node.allow_abbrev,
                "actions": actions,
                "handler": _handler_label(node),
            }
        )
    commands.sort(key=lambda item: item["path"])
    return {"commands": commands}


def test_parser_tree_matches_contract_fixture() -> None:
    """Ref #7547 AC1 — normalized parser tree matches the frozen contract."""
    from ghdag.cli.main import _build_parser

    expected = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    actual = normalize_parser_tree(_build_parser())
    assert actual == expected


def test_build_parser_function_body_within_line_budget() -> None:
    """Ref #7547 AC2 — _build_parser stays small enough to remain in main.py."""
    from ghdag.cli.main import _build_parser

    lines, start = inspect.getsourcelines(_build_parser)
    assert len(lines) <= 80, f"_build_parser is {len(lines)} lines (starts at {start})"
