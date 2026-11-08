"""ghdag schema list / show commands."""

from __future__ import annotations

import json

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
