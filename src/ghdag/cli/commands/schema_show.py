"""ghdag schema list / show commands."""

from __future__ import annotations

import argparse
import json
from typing import cast

from ghdag.schemas import SCHEMA_NAMES, load_schema


def cmd_schema_list(args) -> None:
    """List bundled JSON Schema names."""
    if args.output_json:
        schemas = []
        for name in SCHEMA_NAMES:
            data = load_schema(name)
            schemas.append(
                {
                    "name": name,
                    "title": data.get("title", ""),
                    "description": data.get("description", ""),
                }
            )
        print(json.dumps({"schemas": schemas}, ensure_ascii=False))
        return

    for name in SCHEMA_NAMES:
        print(name)


def cmd_schema_show(args) -> None:
    """Print one bundled JSON Schema."""
    schema = load_schema(args.name)
    print(json.dumps(schema, indent=2, ensure_ascii=False))


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    schema_parser = subparsers.add_parser(
        "schema",
        allow_abbrev=False,
        help="Bundled JSON Schema utilities",
    )
    schema_subparsers = schema_parser.add_subparsers(title="schema subcommands")

    schema_list_parser = schema_subparsers.add_parser(
        "list",
        allow_abbrev=False,
        help="List bundled schema names",
    )
    schema_list_parser.set_defaults(func=cmd_schema_list)

    schema_show_parser = schema_subparsers.add_parser(
        "show",
        allow_abbrev=False,
        help="Print one bundled schema as JSON",
    )
    schema_show_parser.add_argument(
        "name",
        help="Schema logical name (without .schema.json)",
    )
    schema_show_parser.set_defaults(func=cmd_schema_show)
    return cast(argparse.ArgumentParser, schema_parser)
