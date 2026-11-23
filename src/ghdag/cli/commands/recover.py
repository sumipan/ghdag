"""ghdag dag recover command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

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


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    recover_parser = subparsers.add_parser(
        "recover",
        allow_abbrev=False,
        help="Recover failed or pending steps from an existing handler run",
    )
    recover_parser.add_argument("--issue", type=int, required=True, dest="issue_number")
    recover_parser.add_argument("--handler", required=True, help="Handler name (e.g. impl)")
    recover_parser.add_argument(
        "--from",
        dest="from_step",
        default=None,
        metavar="STEP_NAME",
        help="Only recover this step and its downstream dependents",
    )
    recover_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show recover targets without modifying done markers",
    )
    recover_parser.add_argument(
        "--workflows-dir",
        default="workflows",
        dest="workflows_dir",
        metavar="PATH",
        help="Path to workflows YAML directory (default: workflows)",
    )
    recover_parser.add_argument(
        "--exec-jsonl",
        default="jobs/exec.jsonl",
        dest="exec_jsonl",
        metavar="PATH",
        help="Path to exec.jsonl (default: jobs/exec.jsonl)",
    )
    recover_parser.add_argument(
        "--workflow",
        default=None,
        metavar="NAME",
        help="Workflow name (auto-detected if only one workflow exists)",
    )
    recover_parser.add_argument(
        "--state-dir",
        default=None,
        metavar="PATH",
        help="Pipeline state directory (default: $GHDAG_STATE_DIR/.pipeline-state or .pipeline-state)",
    )
    recover_parser.add_argument(
        "--keep-results",
        action="store_true",
        help="Do not archive existing result files (keep original behaviour)",
    )
    recover_parser.set_defaults(func=cmd_recover)
    return cast(argparse.ArgumentParser, recover_parser)
