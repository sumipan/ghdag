"""ghdag trigger command."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ghdag.config.env import state_dir as resolve_state_dir
from ghdag.core.exceptions import CliUsageError, ConfigError


def _load_workflow_for_cli(args, *, workflows_path: Path):
    from ghdag.workflow.loader import load_workflows

    if not workflows_path.exists():
        raise CliUsageError(
            f"directory not found: {workflows_path}",
            code="WORKFLOWS_DIR_NOT_FOUND",
            hint="pass --workflows-dir PATH",
        )

    workflows = load_workflows(workflows_path)
    if not workflows:
        raise ConfigError(
            "no workflow definitions found",
            code="WORKFLOW_NONE_FOUND",
        )

    if args.workflow:
        matching = [w for w in workflows if w.name == args.workflow]
        if not matching:
            names = ", ".join(w.name for w in workflows)
            raise CliUsageError(
                f"workflow '{args.workflow}' not found",
                code="WORKFLOW_NOT_FOUND",
                hint=f"available: {names}",
            )
        workflow = matching[0]
    elif len(workflows) == 1:
        workflow = workflows[0]
    else:
        names = ", ".join(w.name for w in workflows)
        raise CliUsageError(
            f"multiple workflows found ({names}), specify --workflow",
            code="WORKFLOW_AMBIGUOUS",
            hint=f"specify --workflow with one of: {names}",
        )

    handler_name = args.handler
    if handler_name not in workflow.handlers:
        available = ", ".join(workflow.handlers.keys())
        raise CliUsageError(
            f"handler '{handler_name}' not found in workflow '{workflow.name}'",
            code="HANDLER_NOT_FOUND",
            hint=f"available: {available}",
        )

    return workflow, handler_name


def cmd_trigger(args) -> None:
    """ghdag trigger: run a handler one-shot against an Issue."""
    from ghdag.pipeline.llm_pipeline import build_order_builders
    from ghdag.pipeline.task_queue import TaskQueue
    from ghdag.workflow.dispatcher import WorkflowDispatcher

    workflows_path = Path(args.workflows_dir)
    workflow, handler_name = _load_workflow_for_cli(args, workflows_path=workflows_path)
    handler = workflow.handlers[handler_name]

    trigger = None
    trigger_rank = 0
    for rank, t in enumerate(workflow.triggers):
        if t.handler == handler_name:
            trigger = t
            trigger_rank = rank
            break

    from ghdag.forge import get_forges

    github_client = get_forges()[0]
    exec_jsonl_resolved = Path(args.exec_jsonl).resolve()
    queue_dir = str(exec_jsonl_resolved.parent)
    if isinstance(args.state_dir, str):
        state_dir = args.state_dir
    elif exec_jsonl_resolved.parent.name == "jobs":
        state_dir = str(resolve_state_dir(exec_jsonl_resolved.parent.parent) / ".pipeline-state")
    else:
        state_dir = str(resolve_state_dir(".") / ".pipeline-state")
    order_builders = build_order_builders([workflow])
    order_builder = order_builders[workflow.name]

    task_queue = TaskQueue(
        state_dir=state_dir,
        exec_jsonl_path=str(exec_jsonl_resolved),
        order_builder=order_builder,
        queue_dir=queue_dir,
        order_builders=order_builders,
    )
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github_client,
        task_queue=task_queue,
        queue_dir=queue_dir,
    )

    issue = github_client.issue_get(args.issue_number)

    result = dispatcher.dispatch(
        issue,
        workflow,
        handler,
        trigger=trigger,
        trigger_rank=trigger_rank,
        redispatch=args.redispatch,
        redispatch_reason=args.reason,
    )

    if args.output_json:
        payload = {
            "issue": args.issue_number,
            "handler": handler_name,
            "status": result.status,
            "reason": result.reason or None,
            "exec_lines": len(result.exec_lines),
        }
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"trigger: issue #{args.issue_number} handler={handler_name} → {result.status}")
        if result.reason:
            print(f"  reason: {result.reason}")
        if result.status == "dispatched":
            print(f"  exec_lines: {len(result.exec_lines)} step(s)")

    if result.status != "dispatched":
        sys.exit(1)


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    trigger_parser = subparsers.add_parser(
        "trigger",
        allow_abbrev=False,
        help="Trigger a workflow handler for a specific issue (one-shot dispatch)",
    )
    trigger_parser.add_argument("issue_number", type=int, help="GitHub Issue number")
    trigger_parser.add_argument(
        "--handler",
        required=True,
        help="Handler name to execute (e.g. 'impl', 'brushup')",
    )
    trigger_parser.add_argument(
        "--workflows-dir",
        default="workflows",
        dest="workflows_dir",
        metavar="PATH",
        help="Path to workflows YAML directory (default: workflows)",
    )
    trigger_parser.add_argument(
        "--exec-jsonl",
        default="jobs/exec.jsonl",
        dest="exec_jsonl",
        metavar="PATH",
        help="Output path for exec.jsonl (default: jobs/exec.jsonl)",
    )
    trigger_parser.add_argument(
        "--workflow",
        default=None,
        metavar="NAME",
        help="Workflow name (auto-detected if only one workflow exists)",
    )
    trigger_parser.add_argument(
        "--redispatch",
        action="store_true",
        help="Increment generation and start a new handler run",
    )
    trigger_parser.add_argument(
        "--reason",
        default=None,
        help="Reason for redispatch (recorded in audit.jsonl)",
    )
    trigger_parser.add_argument(
        "--state-dir",
        default=None,
        metavar="PATH",
        help="Pipeline state directory (default: derived from --exec-jsonl parent's parent / .pipeline-state)",
    )
    trigger_parser.set_defaults(func=cmd_trigger)
    return trigger_parser
