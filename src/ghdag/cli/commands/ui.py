"""ghdag ui command."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast


def cmd_ui(args) -> None:
    """ghdag ui: launch the Web UI dashboard."""
    from ghdag.ui.server import run_server

    repo_root = Path(args.repo_root).resolve()

    run_server(
        repo_root=repo_root,
        host=args.host,
        port=args.port,
        poll_interval=args.interval,
        max_visible=args.max_visible,
    )


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    ui_parser = subparsers.add_parser(
        "ui", allow_abbrev=False, help="Launch Web UI dashboard",
    )
    ui_parser.add_argument(
        "--repo-root",
        default=".",
        metavar="PATH",
        help="Repository root containing jobs/exec.jsonl (default: .)",
    )
    ui_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1)",
    )
    ui_parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Bind port (default: 8080)",
    )
    ui_parser.add_argument(
        "--interval-sec",
        type=float,
        default=3.0,
        dest="interval",
        metavar="SEC",
        help="SSE poll interval in seconds (default: 3.0)",
    )
    ui_parser.add_argument(
        "--max-visible",
        type=int,
        default=30,
        metavar="N",
        help="Maximum number of tasks to display (default: 30)",
    )
    ui_parser.set_defaults(func=cmd_ui)
    return cast(argparse.ArgumentParser, ui_parser)
