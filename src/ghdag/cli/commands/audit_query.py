"""ghdag audit-query コマンド。"""

from __future__ import annotations

import sys


def cmd_audit_query(args) -> None:
    """ghdag audit-query: audit.jsonl の相関イベント照会またはバースト検出。"""
    import json
    from datetime import datetime
    from pathlib import Path

    from ghdag.pipeline.audit_query import (
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

    audit_path = Path(args.audit_path)

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
