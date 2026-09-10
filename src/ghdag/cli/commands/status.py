"""ghdag status コマンド — Issue / running タスクの現在状態を表示する。"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path


def cmd_status(args) -> None:
    """ghdag status: Issue DAG 状態または running タスク一覧を出力する。"""
    from ghdag.status import issue_status, running_tasks

    if not args.issue_number and not args.running:
        print(
            "error: specify --issue N and/or --running",
            file=sys.stderr,
        )
        sys.exit(1)

    exec_jsonl = Path(
        args.exec_jsonl
        or os.environ.get("GHDAG_EXEC_JSONL")
        or "jobs/exec.jsonl"
    ).resolve()
    queue_dir = exec_jsonl.parent
    done_dir = Path(args.done_dir).resolve() if args.done_dir else queue_dir / "done"
    running_dir = (
        Path(args.running_dir).resolve()
        if args.running_dir
        else queue_dir / "running"
    )
    state_dir = Path(args.state_dir).resolve()
    audit_path = Path(args.audit_path).resolve() if args.audit_path else None

    if args.running and not args.issue_number:
        tasks = running_tasks(running_dir)
        payload = [asdict(t) for t in tasks]
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            if not tasks:
                print("(no running tasks)")
            for t in tasks:
                print(
                    f"{t.uuid} engine={t.engine or '-'} pid={t.pid or '-'} "
                    f"started_at={t.started_at} elapsed_sec={t.elapsed_sec:.1f}"
                )
        return

    if not args.handler or not args.workflow:
        print(
            "error: --handler and --workflow are required with --issue",
            file=sys.stderr,
        )
        sys.exit(1)

    running_uuids: set[str] = set()
    if running_dir.is_dir():
        running_uuids = {p.stem for p in running_dir.glob("*.json")}

    status = issue_status(
        args.issue_number,
        handler=args.handler,
        workflow=args.workflow,
        exec_jsonl_path=exec_jsonl,
        state_dir=state_dir,
        done_dir=done_dir,
        running_uuids=running_uuids,
        audit_path=audit_path,
        running_dir=running_dir,
    )

    if args.json:
        body = {
            "generation": status.generation,
            "running": status.running,
            "steps": [asdict(s) for s in status.steps],
            "orphan_uuids": status.orphan_uuids,
        }
        print(json.dumps(body, ensure_ascii=False, indent=2))
    else:
        print(f"generation: {status.generation}")
        print(f"running: {status.running}")
        print(f"orphan_uuids: {', '.join(status.orphan_uuids) or '-'}")
        for step in status.steps:
            deps = ", ".join(step.depends) if step.depends else "-"
            print(
                f"  {step.step_name} uuid={step.uuid} status={step.status} "
                f"depends=[{deps}]"
            )

    if args.running:
        tasks = running_tasks(running_dir)
        if args.json:
            # Already printed issue JSON; append running as a second document
            # only in text mode to keep --json single-object for --issue.
            pass
        else:
            print("")
            print(f"running tasks: {len(tasks)}")
            for t in tasks:
                print(
                    f"  {t.uuid} engine={t.engine or '-'} "
                    f"elapsed_sec={t.elapsed_sec:.1f}"
                )
