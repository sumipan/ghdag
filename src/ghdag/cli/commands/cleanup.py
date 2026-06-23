"""ghdag cleanup コマンド。"""

from __future__ import annotations

from pathlib import Path


def cmd_cleanup(args) -> None:
    """ghdag cleanup: queue/ のクリーンアップ。"""
    from ghdag.cleanup import cleanup_queue

    repo_root = Path(args.repo_root).resolve()
    result = cleanup_queue(
        queue_dir=repo_root / "jobs",
        archive_dir=repo_root / "jobs" / "archive",
        done_dir=repo_root / "jobs" / "done",
        exec_md=repo_root / "jobs" / "exec.jsonl",
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
