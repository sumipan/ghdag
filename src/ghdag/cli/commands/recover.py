"""ghdag dag recover command."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ghdag.cli.commands.trigger import _load_workflow_for_cli
from ghdag.config.env import state_dir as resolve_state_dir
from ghdag.dag.recover import execute_recover, format_recover_plan, plan_recover, running_uuids_from_queue_dir


def cmd_recover(args) -> None:
    """ghdag dag recover: make failed and not-yet-run steps of an existing run re-runnable."""
    workflows_path = Path(args.workflows_dir)
    workflow, handler_name = _load_workflow_for_cli(args, workflows_path=workflows_path)

    exec_jsonl_resolved = Path(args.exec_jsonl).resolve()
    queue_dir = exec_jsonl_resolved.parent
    queue_state_dir = resolve_state_dir(queue_dir)
    done_dir = queue_state_dir / "done"
    running_uuids = running_uuids_from_queue_dir(queue_state_dir)

    plan = plan_recover(
        state_dir=args.state_dir or str(resolve_state_dir(".") / ".pipeline-state"),
        exec_jsonl_path=str(exec_jsonl_resolved),
        workflow_name=workflow.name,
        handler_name=handler_name,
        issue_number=args.issue_number,
        queue_dir=queue_dir,
        done_dir=done_dir,
        from_step=args.from_step,
        running_uuids=running_uuids,
    )

    keep_results = getattr(args, "keep_results", False)

    if args.dry_run:
        result = execute_recover(
            plan,
            queue_dir=queue_dir,
            done_dir=done_dir,
            dry_run=True,
            running_uuids=running_uuids,
            keep_results=keep_results,
        )
        if getattr(args, "output_json", False):
            payload = {
                "rerun_uuids": list(plan.rerun_uuids),
                "archived": [[src, dst] for src, dst in result.archived],
                "dry_run": True,
                "warnings": list(result.warnings),
            }
            print(json.dumps(payload, ensure_ascii=False))
            return
        print(format_recover_plan(plan))
        for src, dst in result.archived:
            print(f"would archive: {src} -> {dst}")
        print(f"\nrecover dry-run: {len(plan.rerun_uuids)} step(s) would be re-executed")
        return

    result = execute_recover(
        plan,
        queue_dir=queue_dir,
        done_dir=done_dir,
        dry_run=False,
        running_uuids=running_uuids,
        keep_results=keep_results,
    )

    if getattr(args, "output_json", False):
        payload = {
            "rerun_uuids": list(plan.rerun_uuids),
            "archived": [[src, dst] for src, dst in result.archived],
            "dry_run": False,
            "warnings": list(result.warnings),
        }
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)

        for src, dst in result.archived:
            print(f"archived: {src} -> {dst}")

        print(
            f"recover: issue #{args.issue_number} handler={handler_name} "
            f"→ {result.recovered} step(s) reset for re-execution"
        )
