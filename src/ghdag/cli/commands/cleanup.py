"""ghdag cleanup command."""

from __future__ import annotations

from pathlib import Path

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
        exec_md=paths.queue_dir / "exec.jsonl",
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
