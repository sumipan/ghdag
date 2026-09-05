"""ghdag CLI — argparse ベースのエントリポイント。"""

from __future__ import annotations

import argparse
import logging
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
    from ghdag.cli.commands.audit_query import cmd_audit_query
    from ghdag.cli.commands.cleanup import cmd_cleanup
    from ghdag.cli.commands.llm import cmd_llm
    from ghdag.cli.commands.quota import (
        cmd_quota_clear,
        cmd_quota_drain,
        cmd_quota_report,
        cmd_quota_resume,
        cmd_quota_status,
    )
    from ghdag.cli.commands.recover import cmd_recover
    from ghdag.cli.commands.run import cmd_run
    from ghdag.cli.commands.trigger import cmd_trigger
    from ghdag.cli.commands.ui import cmd_ui
    from ghdag.cli.commands.watch import cmd_watch

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
    run_parser = subparsers.add_parser("run", help="Run exec.jsonl via DagEngine")
    run_parser.add_argument("exec_jsonl", help="Path to exec.jsonl file")
    run_parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        metavar="SEC",
        help="Poll interval in seconds (default: 1.0)",
    )
    run_parser.add_argument(
        "--hooks",
        default=None,
        metavar="MODULE",
        help="Python module path for DagHooks implementation (e.g. scripts.diary_hooks)",
    )
    run_parser.add_argument(
        "--max-concurrency",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of concurrent tasks (default: unlimited)",
    )
    run_parser.set_defaults(func=cmd_run)

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
    watch_parser.set_defaults(func=cmd_watch)

    # ghdag ui
    ui_parser = subparsers.add_parser("ui", help="Launch Web UI dashboard")
    ui_parser.add_argument(
        "--repo-root",
        default=".",
        metavar="PATH",
        help="Repository root containing jobs/exec.jsonl (default: .)",
    )
    ui_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1)",
    )
    ui_parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Bind port (default: 8080)",
    )
    ui_parser.add_argument(
        "--interval",
        type=float,
        default=3.0,
        metavar="SEC",
        help="SSE poll interval in seconds (default: 3.0)",
    )
    ui_parser.add_argument(
        "--max-visible",
        type=int,
        default=30,
        metavar="N",
        help="Maximum number of tasks to display (default: 30)",
    )
    ui_parser.set_defaults(func=cmd_ui)

    # ghdag llm
    llm_parser = subparsers.add_parser(
        "llm",
        help="One-shot LLM call without workflow",
    )
    llm_parser.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help="Prompt text (reads from stdin if omitted)",
    )
    llm_parser.add_argument(
        "--engine", "-e",
        default="claude",
        help="LLM engine name (default: claude)",
    )
    llm_parser.add_argument(
        "--model", "-m",
        default=None,
        help="Model ID (default: engine default)",
    )
    llm_parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        metavar="SEC",
        help="Timeout in seconds (default: no limit)",
    )
    llm_parser.add_argument(
        "--dangerously-skip-permissions",
        action="store_true",
        dest="dangerously_skip_permissions",
        help="Pass --dangerously-skip-permissions to claude CLI",
    )
    llm_parser.add_argument(
        "--permission-mode",
        default=None,
        dest="permission_mode",
        choices=["default", "plan", "bypassPermissions"],
        help="Permission mode for Claude engine",
    )
    llm_parser.add_argument(
        "--capabilities-preset",
        default=None,
        dest="capabilities_preset",
        choices=["text_only", "json_only", "web_research", "dangerous_full_access"],
        help="Capabilities preset name",
    )
    llm_parser.add_argument(
        "--stdin",
        action="store_true",
        dest="read_stdin",
        help="Also pipe stdin content to the LLM process",
    )
    llm_parser.add_argument(
        "--list-engines",
        action="store_true",
        dest="list_engines",
        help="List available engines and exit",
    )
    llm_parser.add_argument(
        "--list-models",
        action="store_true",
        dest="list_models",
        help="List available models for the specified engine and exit",
    )
    llm_parser.add_argument(
        "--audit-path",
        default=None,
        dest="audit_path",
        help="Path to audit.jsonl (env: GHDAG_AUDIT_PATH)",
    )
    llm_parser.add_argument(
        "--correlation-id",
        default=None,
        dest="correlation_id",
        help="Correlation ID for audit log",
    )
    llm_parser.add_argument(
        "--request-id",
        default=None,
        dest="request_id",
        help="Request ID for audit log (propagated from orchestrator)",
    )
    llm_parser.set_defaults(func=cmd_llm)

    # ghdag version
    version_parser = subparsers.add_parser("version", help="Show version and exit")
    version_parser.set_defaults(func=_cmd_version)

    # ghdag cleanup
    cleanup_parser = subparsers.add_parser("cleanup", help="Archive completed/orphaned queue tasks")
    cleanup_parser.add_argument("repo_root", help="Path to repository root")
    cleanup_parser.add_argument("--dry-run", action="store_true", help="Show targets without making changes")
    cleanup_parser.add_argument("--cutoff-days", type=int, default=1, help="Days before archiving completed tasks (default: 1)")
    cleanup_parser.add_argument("--orphan-days", type=int, default=7, help="Days before archiving orphaned tasks (default: 7)")
    cleanup_parser.add_argument("--auto-repair", action="store_true", help="Auto-fix orphan (Case D) and dead entry (Case F) issues; default is detect-only")
    cleanup_parser.set_defaults(func=cmd_cleanup)

    # ghdag trigger
    trigger_parser = subparsers.add_parser(
        "trigger",
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
        "--exec-md",
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
    trigger_parser.set_defaults(func=cmd_trigger)

    # ghdag dag recover
    dag_parser = subparsers.add_parser("dag", help="DAG utilities")
    dag_subparsers = dag_parser.add_subparsers(title="dag subcommands")

    recover_parser = dag_subparsers.add_parser(
        "recover",
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
        "--exec-md",
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
        default=".pipeline-state",
        metavar="PATH",
        help="Pipeline state directory (default: .pipeline-state)",
    )
    recover_parser.set_defaults(func=cmd_recover)

    # ghdag audit-query
    audit_query_parser = subparsers.add_parser(
        "audit-query",
        help="Query audit.jsonl for correlation events or burst detection",
    )
    audit_query_parser.add_argument(
        "--correlation-id",
        default=None,
        dest="correlation_id",
        help="Filter events by correlation ID",
    )
    audit_query_parser.add_argument(
        "--burst-detect",
        action="store_true",
        dest="burst_detect",
        help="Detect correlation ID bursts (exit code 1 if found)",
    )
    audit_query_parser.add_argument(
        "--since",
        default=None,
        help="ISO 8601 datetime filter (correlation-id mode only)",
    )
    audit_query_parser.add_argument(
        "--audit-path",
        default="jobs/audit.jsonl",
        dest="audit_path",
        help="Path to audit.jsonl (default: jobs/audit.jsonl)",
    )
    audit_query_parser.add_argument(
        "--window-sec",
        type=float,
        default=600.0,
        dest="window_sec",
        help="Burst detection window in seconds (default: 600)",
    )
    audit_query_parser.add_argument(
        "--threshold",
        type=int,
        default=10,
        help="Burst detection threshold (default: 10)",
    )
    audit_query_parser.set_defaults(func=cmd_audit_query)

    # ghdag tools
    from ghdag.tool.cli import cmd_tools_list

    tools_parser = subparsers.add_parser("tools", help="Tool definition management")
    tools_subparsers = tools_parser.add_subparsers(title="tools subcommands")

    list_parser = tools_subparsers.add_parser("list", help="List tool definitions")
    list_parser.add_argument(
        "--path",
        required=True,
        help="Tool definition directory",
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output as JSON",
    )
    list_parser.set_defaults(func=cmd_tools_list)

    # ghdag quota
    quota_parser = subparsers.add_parser("quota", help="Manage quota gate state")
    quota_subparsers = quota_parser.add_subparsers(title="quota subcommands")

    quota_report = quota_subparsers.add_parser("report", help="Report engine quota state")
    quota_report.add_argument("engine", help="Engine name (e.g. claude)")
    quota_report.add_argument(
        "--status",
        required=True,
        choices=["available", "paused"],
        help="Quota status",
    )
    quota_report.add_argument(
        "--observed-at",
        required=True,
        dest="observed_at",
        help="Observed time in ISO8601 with timezone",
    )
    quota_report.add_argument(
        "--resume-at",
        default=None,
        dest="resume_at",
        help="Optional resume time in ISO8601 with timezone",
    )
    quota_report.add_argument(
        "--reason",
        default=None,
        help="Optional reason",
    )
    quota_report.add_argument(
        "--state-path",
        default="jobs/quota-gate.json",
        dest="state_path",
        help="Path to quota state JSON",
    )
    quota_report.set_defaults(func=cmd_quota_report)

    quota_clear = quota_subparsers.add_parser("clear", help="Clear engine quota state")
    quota_clear.add_argument("engine", help="Engine name")
    quota_clear.add_argument(
        "--observed-at",
        required=True,
        dest="observed_at",
        help="Observed time in ISO8601 with timezone",
    )
    quota_clear.add_argument(
        "--state-path",
        default="jobs/quota-gate.json",
        dest="state_path",
        help="Path to quota state JSON",
    )
    quota_clear.set_defaults(func=cmd_quota_clear)

    quota_drain = quota_subparsers.add_parser("drain", help="Start engine drain mode")
    quota_drain.add_argument("engine", help="Engine name")
    quota_drain.add_argument(
        "--reason",
        default=None,
        help="Optional drain reason",
    )
    quota_drain.add_argument(
        "--state-path",
        default="jobs/quota-gate.json",
        dest="state_path",
        help="Path to quota state JSON",
    )
    quota_drain.set_defaults(func=cmd_quota_drain)

    quota_resume = quota_subparsers.add_parser("resume", help="Resume engine from drain mode")
    quota_resume.add_argument("engine", help="Engine name")
    quota_resume.add_argument(
        "--state-path",
        default="jobs/quota-gate.json",
        dest="state_path",
        help="Path to quota state JSON",
    )
    quota_resume.set_defaults(func=cmd_quota_resume)

    quota_status = quota_subparsers.add_parser("status", help="Show quota state snapshot")
    quota_status.add_argument(
        "--state-path",
        default="jobs/quota-gate.json",
        dest="state_path",
        help="Path to quota state JSON",
    )
    quota_status.add_argument(
        "--exec-path",
        default="jobs/exec.jsonl",
        dest="exec_path",
        help="Path to exec.jsonl",
    )
    quota_status.add_argument(
        "--done-dir",
        default="jobs/done",
        dest="done_dir",
        help="Path to done marker directory",
    )
    quota_status.set_defaults(func=cmd_quota_status)

    return parser


def _setup_logging(args: argparse.Namespace) -> None:
    if args.verbose:
        level = logging.DEBUG
    elif args.quiet:
        level = logging.WARNING
    else:
        level = logging.INFO
    logging.basicConfig(level=level, force=True)


def _cmd_version(args: argparse.Namespace) -> None:
    """stdout に __version__ を出力。"""
    from ghdag import __version__

    print(__version__)
