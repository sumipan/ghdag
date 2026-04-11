"""ghdag CLI — argparse ベースのエントリポイント。"""

from __future__ import annotations

import argparse
import logging
import os
import sys


def main(argv: list[str] | None = None) -> None:
    """CLI メインエントリポイント。argv=None → sys.argv[1:]。テスト時に引数注入可能。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args)
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(0)
    args.func(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghdag",
        description="Generic DAG execution engine",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress INFO logging (WARNING and above only)",
    )

    subparsers = parser.add_subparsers(title="subcommands")

    # ghdag run
    run_parser = subparsers.add_parser("run", help="Run exec.md via DagEngine")
    run_parser.add_argument("exec_md", help="Path to exec.md file")
    run_parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        metavar="SEC",
        help="Poll interval in seconds (default: 1.0)",
    )
    run_parser.set_defaults(func=_cmd_run)

    # ghdag watch
    watch_parser = subparsers.add_parser(
        "watch", help="Watch workflows directory via WorkflowDispatcher"
    )
    watch_parser.add_argument("workflows_dir", help="Path to workflows YAML directory")
    watch_parser.add_argument(
        "--interval",
        type=float,
        default=30.0,
        metavar="SEC",
        help="GitHub polling interval in seconds (default: 30)",
    )
    watch_parser.add_argument(
        "--exec-md",
        default="exec.md",
        dest="exec_md",
        metavar="PATH",
        help="Output path for exec.md (default: exec.md)",
    )
    watch_parser.set_defaults(func=_cmd_watch)

    # ghdag version
    version_parser = subparsers.add_parser("version", help="Show version and exit")
    version_parser.set_defaults(func=_cmd_version)

    return parser


def _setup_logging(args: argparse.Namespace) -> None:
    if args.verbose:
        level = logging.DEBUG
    elif args.quiet:
        level = logging.WARNING
    else:
        level = logging.INFO
    logging.basicConfig(level=level, force=True)


def _cmd_run(args: argparse.Namespace) -> None:
    """DagConfig を構築し DagEngine.run() を呼ぶ薄いラッパー。"""
    if not os.path.exists(args.exec_md):
        print(f"error: file not found: {args.exec_md}", file=sys.stderr)
        sys.exit(1)

    from ghdag.dag.engine import DagEngine
    from ghdag.dag.models import DagConfig

    config = DagConfig(exec_md_path=args.exec_md, poll_interval=args.interval)
    DagEngine(config).run()


def _cmd_watch(args: argparse.Namespace) -> None:
    """WorkflowDispatcher を構築し run() を呼ぶ薄いラッパー。"""
    from pathlib import Path

    from ghdag.pipeline.order import TemplateOrderBuilder
    from ghdag.pipeline.state import PipelineState
    from ghdag.workflow.dispatcher import WorkflowDispatcher
    from ghdag.workflow.github import GitHubIssueClient
    from ghdag.workflow.loader import load_workflows

    workflows_path = Path(args.workflows_dir)
    if not workflows_path.exists():
        print(f"error: directory not found: {workflows_path}", file=sys.stderr)
        sys.exit(1)

    workflows = load_workflows(workflows_path)
    for wf in workflows:
        wf.polling_interval = args.interval

    github_client = GitHubIssueClient()
    pipeline_state = PipelineState(
        state_dir=".pipeline-state",
        exec_md_path=args.exec_md,
    )
    order_builder = TemplateOrderBuilder("templates")

    dispatcher = WorkflowDispatcher(
        workflows=workflows,
        github_client=github_client,
        pipeline_state=pipeline_state,
        order_builder=order_builder,
    )
    dispatcher.run()


def _cmd_version(args: argparse.Namespace) -> None:
    """stdout に __version__ を出力。"""
    from ghdag import __version__

    print(__version__)
