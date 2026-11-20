"""ghdag watch command."""

from __future__ import annotations

import argparse
from pathlib import Path

from ghdag.config.env import state_dir as resolve_state_dir
from ghdag.core.exceptions import CliUsageError


def cmd_watch(args) -> None:
    """Thin wrapper that builds a WorkflowDispatcher and calls run()."""
    from ghdag.pipeline.llm_pipeline import build_order_builders
    from ghdag.pipeline.order import TemplateOrderBuilder
    from ghdag.pipeline.task_queue import TaskQueue
    from ghdag.workflow.dispatcher import WorkflowDispatcher
    from ghdag.workflow.loader import load_workflows

    workflows_path = Path(args.workflows_dir)
    if not workflows_path.exists():
        raise CliUsageError(
            f"directory not found: {workflows_path}",
            code="WORKFLOWS_DIR_NOT_FOUND",
            hint="pass --workflows-dir PATH",
        )

    workflows = load_workflows(workflows_path)
    for wf in workflows:
        wf.polling_interval = args.interval

    from ghdag.forge import get_forges

    github_clients = get_forges(rate_limit_max_wait_sec=0)
    exec_jsonl_resolved = Path(args.exec_jsonl).resolve()
    queue_dir = str(exec_jsonl_resolved.parent)
    if isinstance(args.state_dir, str):
        state_dir = args.state_dir
    elif exec_jsonl_resolved.parent.name == "jobs":
        state_dir = str(resolve_state_dir(exec_jsonl_resolved.parent.parent) / ".pipeline-state")
    else:
        state_dir = str(resolve_state_dir(".") / ".pipeline-state")
    order_builders = build_order_builders(workflows)
    default_order_builder = TemplateOrderBuilder(
        workflows_path.resolve() / "templates"
    )

    task_queue = TaskQueue(
        state_dir=state_dir,
        exec_jsonl_path=str(exec_jsonl_resolved),
        order_builder=default_order_builder,
        queue_dir=queue_dir,
        order_builders=order_builders,
    )
    dispatcher = WorkflowDispatcher(
        workflows=workflows,
        workflows_dir=workflows_path,
        github_client=github_clients,
        task_queue=task_queue,
        queue_dir=queue_dir,
        pause_file=args.pause_file,
    )
    dispatcher.run(max_iterations=1 if args.once else None)


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    watch_parser = subparsers.add_parser(
        "watch",
        allow_abbrev=False,
        help="Watch workflows directory via WorkflowDispatcher",
    )
    watch_parser.add_argument("workflows_dir", help="Path to workflows YAML directory")
    watch_parser.add_argument(
        "--interval-sec",
        type=float,
        default=30.0,
        dest="interval",
        metavar="SEC",
        help="GitHub polling interval in seconds (default: 30)",
    )
    watch_parser.add_argument(
        "--exec-jsonl",
        default="jobs/exec.jsonl",
        dest="exec_jsonl",
        metavar="PATH",
        help="Output path for exec.jsonl (default: jobs/exec.jsonl)",
    )
    watch_parser.add_argument(
        "--once",
        action="store_true",
        help="Poll once and exit (one-shot mode for event-driven triggers)",
    )
    watch_parser.add_argument(
        "--pause-file",
        default=None,
        metavar="PATH",
        help="Pause dispatch while this file exists (default: disabled)",
    )
    watch_parser.add_argument(
        "--state-dir",
        default=None,
        metavar="PATH",
        help="Pipeline state directory (default: derived from --exec-jsonl parent's parent / .pipeline-state)",
    )
    watch_parser.set_defaults(func=cmd_watch)
    return watch_parser
