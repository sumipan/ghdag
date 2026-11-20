"""ghdag audit-query command."""

from __future__ import annotations

import argparse
import sys


def cmd_audit_query(args) -> None:
    """ghdag audit-query: query correlated events in audit.jsonl or detect bursts."""
    import json
    from datetime import datetime
    from pathlib import Path

    from ghdag.config.env import ghdag_audit_path
    from ghdag.io.audit_query import (
        detect_correlation_bursts,
        read_task_exit_events,
    )

    has_cid = args.correlation_id is not None
    has_burst = args.burst_detect

    if has_cid and has_burst:
        print(
            "error: --correlation-id and --burst-detect are mutually exclusive",
            file=sys.stderr,
        )
        sys.exit(2)
    if not has_cid and not has_burst:
        print(
            "error: either --correlation-id or --burst-detect is required",
            file=sys.stderr,
        )
        sys.exit(2)

    audit_path = Path(args.audit_path or ghdag_audit_path() or "jobs/audit.jsonl")

    if has_burst:
        bursts = detect_correlation_bursts(
            audit_path,
            window_sec=args.window_sec,
            threshold=args.threshold,
        )
        print(json.dumps(bursts, ensure_ascii=False))
        if bursts:
            sys.exit(1)
        return

    since_epoch = None
    if args.since is not None:
        try:
            since_epoch = datetime.fromisoformat(args.since).timestamp()
        except ValueError as exc:
            print(f"error: invalid --since datetime: {exc}", file=sys.stderr)
            sys.exit(2)

    events = read_task_exit_events(
        audit_path,
        correlation_id=args.correlation_id,
        since=since_epoch,
    )
    for event in events:
        print(json.dumps(event, ensure_ascii=False))


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    audit_query_parser = subparsers.add_parser(
        "audit-query",
        allow_abbrev=False,
        help="Query audit.jsonl for correlation events or burst detection",
    )
    audit_query_parser.add_argument(
        "--correlation-id",
        default=None,
        dest="correlation_id",
        help="Filter events by correlation ID",
    )
    audit_query_parser.add_argument(
        "--burst-detect",
        action="store_true",
        dest="burst_detect",
        help="Detect correlation ID bursts (exit code 1 if found)",
    )
    audit_query_parser.add_argument(
        "--since",
        default=None,
        help="ISO 8601 datetime filter (correlation-id mode only)",
    )
    audit_query_parser.add_argument(
        "--audit-path",
        default=None,
        dest="audit_path",
        help="Path to audit.jsonl (default: $GHDAG_AUDIT_PATH or jobs/audit.jsonl)",
    )
    audit_query_parser.add_argument(
        "--window-sec",
        type=float,
        default=600.0,
        dest="window_sec",
        help="Burst detection window in seconds (default: 600)",
    )
    audit_query_parser.add_argument(
        "--threshold",
        type=int,
        default=10,
        help="Burst detection threshold (default: 10)",
    )
    audit_query_parser.set_defaults(func=cmd_audit_query)
    return audit_query_parser
