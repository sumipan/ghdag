"""ghdag cleanup command."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from ghdag.config.env import resolve_runtime_paths


def cmd_cleanup(args) -> None:
    """ghdag cleanup: clean up queue/."""
    from ghdag.cleanup.orchestrator import cleanup_queue

    repo_root = Path(args.repo_root).resolve()
    paths = resolve_runtime_paths(repo_root / "jobs" / "exec.jsonl")
    result = cleanup_queue(
        queue_dir=paths.queue_dir,
        archive_dir=paths.queue_dir / "archive",
        done_dir=paths.done_dir,
        exec_jsonl=paths.queue_dir / "exec.jsonl",
        cutoff_days=args.cutoff_days,
        orphan_days=args.orphan_days,
        dry_run=args.dry_run,
        auto_repair=args.auto_repair,
    )
    if args.auto_repair:
        msg = (
            f"cleanup: archived done={result.archived_done}, "
            f"orphan={result.archived_orphan}, "
            f"extras={result.swept_extras}, "
            f"exec pruned={result.pruned_exec}"
        )
    else:
        msg = f"cleanup: archived done={result.archived_done}, extras={result.swept_extras}"
        if result.detected_orphan > 0 or result.detected_dead > 0:
            msg += (
                f" | detected: orphan={result.detected_orphan}, dead={result.detected_dead}"
                " (use --auto-repair to fix)"
            )
    if args.dry_run:
        msg += " [dry-run]"
    print(msg)


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    cleanup_parser = subparsers.add_parser(
        "cleanup",
        allow_abbrev=False,
        help="Archive completed/orphaned queue tasks",
    )
    cleanup_parser.add_argument("repo_root", help="Path to repository root")
    cleanup_parser.add_argument(
        "--dry-run", action="store_true", help="Show targets without making changes",
    )
    cleanup_parser.add_argument(
        "--cutoff-days",
        type=int,
        default=1,
        help="Days before archiving completed tasks (default: 1)",
    )
    cleanup_parser.add_argument(
        "--orphan-days",
        type=int,
        default=7,
        help="Days before archiving orphaned tasks (default: 7)",
    )
    cleanup_parser.add_argument(
        "--auto-repair",
        action="store_true",
        help="Auto-fix orphan (Case D) and dead entry (Case F) issues; default is detect-only",
    )
    cleanup_parser.set_defaults(func=cmd_cleanup)
    return cast(argparse.ArgumentParser, cleanup_parser)
