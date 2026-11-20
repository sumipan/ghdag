"""ghdag dag cancel command."""

from __future__ import annotations

import argparse
from pathlib import Path

from ghdag.config.env import state_dir as resolve_state_dir
from ghdag.core.exceptions import GhdagError
from ghdag.status import read_exclusive_gate


def cmd_cancel(args) -> None:
    """ghdag dag cancel: create the jobs/cancel/<uuid> control file (does not touch processes)."""
    uuid = args.uuid.strip()
    queue_dir = (Path(args.state_dir) if args.state_dir else resolve_state_dir("jobs")).resolve()
    running_path = queue_dir / "running" / f"{uuid}.json"
    if not running_path.is_file():
        gate = read_exclusive_gate(queue_dir)
        if gate != (uuid, "waiting"):
            raise GhdagError(f"not running: {uuid}", code="TASK_NOT_RUNNING")

    cancel_dir = queue_dir / "cancel"
    cancel_dir.mkdir(parents=True, exist_ok=True)
    cancel_path = cancel_dir / uuid
    cancel_path.write_text("", encoding="utf-8")
    print(f"cancel requested: {uuid}")


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    cancel_parser = subparsers.add_parser(
        "cancel",
        allow_abbrev=False,
        help="Request cancellation of a running DAG task via jobs/cancel/<uuid>",
    )
    cancel_parser.add_argument("uuid", help="Task UUID to cancel")
    cancel_parser.add_argument(
        "--state-dir",
        default=None,
        dest="state_dir",
        metavar="DIR",
        help="State directory containing running/ and cancel/ (default: $GHDAG_STATE_DIR or jobs)",
    )
    cancel_parser.set_defaults(func=cmd_cancel)
    return cancel_parser
