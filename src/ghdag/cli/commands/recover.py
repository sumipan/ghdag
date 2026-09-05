"""ghdag dag recover コマンド。"""

from __future__ import annotations

import sys
from pathlib import Path


def cmd_recover(args) -> None:
    """ghdag dag recover: 既存 run の失敗・未実行ステップを再実行可能にする。"""
    from ghdag.dag.recover import (
        RecoverError,
        execute_recover,
        format_recover_plan,
        plan_recover,
    )
    from ghdag.workflow.loader import load_workflows

    workflows_path = Path(args.workflows_dir)
    if not workflows_path.exists():
        print(f"error: directory not found: {workflows_path}", file=sys.stderr)
        sys.exit(1)

    workflows = load_workflows(workflows_path)
    if not workflows:
        print("error: no workflow definitions found", file=sys.stderr)
        sys.exit(1)

    if args.workflow:
        matching = [w for w in workflows if w.name == args.workflow]
        if not matching:
            print(f"error: workflow '{args.workflow}' not found", file=sys.stderr)
            sys.exit(1)
        workflow = matching[0]
    elif len(workflows) == 1:
        workflow = workflows[0]
    else:
        names = ", ".join(w.name for w in workflows)
        print(
            f"error: multiple workflows found ({names}), specify --workflow",
            file=sys.stderr,
        )
        sys.exit(1)

    handler_name = args.handler
    if handler_name not in workflow.handlers:
        available = ", ".join(workflow.handlers.keys())
        print(
            f"error: handler '{handler_name}' not found in workflow '{workflow.name}' "
            f"(available: {available})",
            file=sys.stderr,
        )
        sys.exit(1)

    exec_jsonl_resolved = Path(args.exec_jsonl).resolve()
    queue_dir = exec_jsonl_resolved.parent
    done_dir = queue_dir / "done"

    try:
        plan = plan_recover(
            state_dir=args.state_dir,
            exec_jsonl_path=str(exec_jsonl_resolved),
            workflow_name=workflow.name,
            handler_name=handler_name,
            issue_number=args.issue_number,
            queue_dir=queue_dir,
            done_dir=done_dir,
            from_step=args.from_step,
        )
    except RecoverError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(format_recover_plan(plan))
        print(f"\nrecover dry-run: {len(plan.rerun_uuids)} step(s) would be re-executed")
        return

    try:
        result = execute_recover(
            plan,
            queue_dir=queue_dir,
            done_dir=done_dir,
            dry_run=False,
        )
    except RecoverError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)

    print(
        f"recover: issue #{args.issue_number} handler={handler_name} "
        f"→ {result.recovered} step(s) reset for re-execution"
    )
