"""ghdag.tool.cli — CLI handlers for tool subcommands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ghdag.tool.exceptions import ToolRegistryError
from ghdag.tool.registry import ToolRegistry
from ghdag.tool.schema import ToolDef


def _tool_to_dict(tool: ToolDef) -> dict:
    return {
        "name": tool.name,
        "engine": tool.engine,
        "model": tool.model,
        "fallback": [
            {"engine": fb.engine, "model": fb.model}
            for fb in tool.fallback
        ],
    }


def cmd_tools_list(args: argparse.Namespace) -> None:
    path = Path(args.path)
    try:
        tools = ToolRegistry.discover(path)
    except FileNotFoundError:
        print(f"error: Directory not found: {path}", file=sys.stderr)
        sys.exit(1)
    except ToolRegistryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.output_json:
        payload = {
            "tools": [
                _tool_to_dict(t)
                for t in sorted(tools.values(), key=lambda t: t.name)
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for name in sorted(tools.keys()):
            tool = tools[name]
            print(f"{tool.name}: {tool.engine}/{tool.model}")
