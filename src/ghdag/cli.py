"""ghdag CLI — argparse ベースのエントリポイント。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ghdag.dag.hooks import DagHooks


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
    watch_parser.set_defaults(func=_cmd_watch)

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
    ui_parser.set_defaults(func=_cmd_ui)

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
    llm_parser.set_defaults(func=_cmd_llm)

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
    cleanup_parser.set_defaults(func=_cmd_cleanup)

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
    trigger_parser.set_defaults(func=_cmd_trigger)

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
    if not os.path.exists(args.exec_jsonl):
        print(f"error: file not found: {args.exec_jsonl}", file=sys.stderr)
        sys.exit(1)

    from ghdag.dag.engine import DagEngine
    from ghdag.dag.models import DagConfig

    cwd = getattr(args, "cwd", None) or str(Path(args.exec_jsonl).resolve().parent.parent)
    config = DagConfig(
        exec_jsonl_path=args.exec_jsonl,
        poll_interval=args.interval,
        cwd=cwd,
        max_concurrency=args.max_concurrency,
    )
    if args.hooks:
        hooks: DagHooks = _load_hooks(args.hooks)
    else:
        from ghdag.pipeline.hooks import AuditHooks
        audit_path = Path(args.exec_jsonl).resolve().parent.parent / "audit.jsonl"
        hooks = AuditHooks(audit_path=audit_path)
    engine = DagEngine(config, hooks)
    if hooks is not None and hasattr(hooks, "set_engine"):
        hooks.set_engine(engine)
    engine.run()


def _load_hooks(module_path: str) -> DagHooks:
    """モジュールパスから DagHooks 実装クラスをインスタンス化して返す。

    クラスの探索順:
    1. モジュールに `HOOKS_CLASS` 属性がある場合はそれを使用
    2. `on_task_success` を持つ最初の公開クラスを使用

    Raises:
        SystemExit: モジュールが見つからない、またはクラスが見つからない場合
    """
    import importlib
    import inspect
    from typing import cast

    from ghdag.dag.hooks import DagHooks

    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        print(f"error: cannot import hooks module '{module_path}': {exc}", file=sys.stderr)
        sys.exit(1)

    if hasattr(module, "HOOKS_CLASS"):
        cls = module.HOOKS_CLASS
        return cast(DagHooks, cls())

    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ == module.__name__ and hasattr(obj, "on_task_success"):
            return cast(DagHooks, obj())

    print(
        f"error: no DagHooks-compatible class found in module '{module_path}'",
        file=sys.stderr,
    )
    sys.exit(1)


def _cmd_trigger(args: argparse.Namespace) -> None:
    """ghdag trigger: Issue に対してワンショットでハンドラーを実行する。"""
    from pathlib import Path

    from ghdag.github_client import create_github_client
    from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
    from ghdag.pipeline.order import TemplateOrderBuilder
    from ghdag.pipeline.state import PipelineState
    from ghdag.workflow.dispatcher import WorkflowDispatcher
    from ghdag.workflow.loader import load_workflows

    workflows_path = Path(args.workflows_dir)
    if not workflows_path.exists():
        print(f"error: directory not found: {workflows_path}", file=sys.stderr)
        sys.exit(1)

    workflows = load_workflows(workflows_path)
    if not workflows:
        print("error: no workflow definitions found", file=sys.stderr)
        sys.exit(1)

    # Resolve workflow
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

    # Resolve handler
    handler_name = args.handler
    if handler_name not in workflow.handlers:
        available = ", ".join(workflow.handlers.keys())
        print(
            f"error: handler '{handler_name}' not found in workflow '{workflow.name}' "
            f"(available: {available})",
            file=sys.stderr,
        )
        sys.exit(1)

    handler = workflow.handlers[handler_name]

    # Resolve trigger and rank
    trigger = None
    trigger_rank = 0
    for rank, t in enumerate(workflow.triggers):
        if t.handler == handler_name:
            trigger = t
            trigger_rank = rank
            break

    github_client = create_github_client()
    exec_jsonl_resolved = Path(args.exec_jsonl).resolve()
    queue_dir = str(exec_jsonl_resolved.parent)
    pipeline_state = PipelineState(
        state_dir=".pipeline-state",
        exec_jsonl_path=str(exec_jsonl_resolved),
    )
    template_dir = workflow.template_dir or "templates"
    order_builder = TemplateOrderBuilder(template_dir)

    pipeline = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=order_builder,
        queue_dir=queue_dir,
    )
    dispatcher = WorkflowDispatcher(
        workflows=[workflow],
        github_client=github_client,
        pipeline=pipeline,
        queue_dir=queue_dir,
    )

    # Fetch issue data from GitHub
    issue = github_client.get_issue(args.issue_number)

    result = dispatcher.dispatch(
        issue, workflow, handler, trigger=trigger, trigger_rank=trigger_rank,
    )
    print(f"trigger: issue #{args.issue_number} handler={handler_name} → {result.status}")
    if result.reason:
        print(f"  reason: {result.reason}")
    if result.status == "dispatched":
        print(f"  exec_lines: {len(result.exec_lines) - 1} step(s)")
    if result.status != "dispatched":
        sys.exit(1)


def _cmd_watch(args: argparse.Namespace) -> None:
    """WorkflowDispatcher を構築し run() を呼ぶ薄いラッパー。"""
    from pathlib import Path
    from typing import cast

    from ghdag.github_client import create_github_clients
    from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
    from ghdag.pipeline.order import OrderBuilder, TemplateOrderBuilder
    from ghdag.pipeline.state import PipelineState
    from ghdag.workflow.dispatcher import WorkflowDispatcher
    from ghdag.workflow.loader import load_workflows

    workflows_path = Path(args.workflows_dir)
    if not workflows_path.exists():
        print(f"error: directory not found: {workflows_path}", file=sys.stderr)
        sys.exit(1)

    workflows = load_workflows(workflows_path)
    for wf in workflows:
        wf.polling_interval = args.interval

    github_clients = create_github_clients()
    exec_jsonl_resolved = Path(args.exec_jsonl).resolve()
    queue_dir = str(exec_jsonl_resolved.parent)
    pipeline_state = PipelineState(
        state_dir=".pipeline-state",
        exec_jsonl_path=str(exec_jsonl_resolved),
    )
    # template_dir: ワークフローごとに OrderBuilder を用意し、submit() 時に
    # base_context["workflow_name"] で切り替える。`watch` はワークフローを
    # 横断するため、単一の OrderBuilder で固定するとワークフロー間の
    # template_dir が混線する（FileNotFoundError の原因）。
    order_builders: dict[str, TemplateOrderBuilder] = {
        wf.name: TemplateOrderBuilder(wf.template_dir or "templates")
        for wf in workflows
    }
    # フォールバック: workflow_name が解決できない場合に備えて、先頭ワークフローの
    # template_dir（無ければ "templates"）を default として保持する。
    default_template_dir = next(
        (wf.template_dir for wf in workflows if wf.template_dir), "templates"
    )
    default_order_builder = TemplateOrderBuilder(default_template_dir)

    pipeline = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=default_order_builder,
        queue_dir=queue_dir,
        order_builders=cast(dict[str, OrderBuilder], order_builders),
    )
    dispatcher = WorkflowDispatcher(
        workflows=workflows,
        github_client=github_clients,
        pipeline=pipeline,
        queue_dir=queue_dir,
    )
    dispatcher.run(max_iterations=1 if args.once else None)


def _cmd_ui(args: argparse.Namespace) -> None:
    """ghdag ui: Web UI ダッシュボードを起動する。"""
    from pathlib import Path

    from ghdag.ui.server import run_server

    repo_root = Path(args.repo_root).resolve()

    run_server(
        repo_root=repo_root,
        host=args.host,
        port=args.port,
        poll_interval=args.interval,
        max_visible=args.max_visible,
    )


def _cmd_llm(args: argparse.Namespace) -> None:
    """ghdag llm: ワンショット LLM 呼び出し（ワークフロー不要）。"""
    from ghdag.llm.engines import (
        EngineModelError,
        call,
        list_engines,
        list_models,
    )

    # --list-engines
    if args.list_engines:
        for engine in list_engines():
            print(engine)
        return

    # --list-models
    if args.list_models:
        try:
            for model in list_models(args.engine):
                print(model)
        except EngineModelError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    # Prompt resolution
    prompt = args.prompt
    if prompt is None:
        if sys.stdin.isatty():
            print("error: prompt required (positional arg or stdin)", file=sys.stderr)
            sys.exit(1)
        prompt = sys.stdin.read().strip()
        if not prompt:
            print("error: empty prompt from stdin", file=sys.stderr)
            sys.exit(1)

    # stdin piping (--stdin flag with separate prompt)
    stdin_text = None
    if args.read_stdin and not sys.stdin.isatty():
        stdin_text = sys.stdin.read()

    # capabilities 解決: --capabilities-preset / --permission-mode の組み合わせ
    capabilities = None
    if args.capabilities_preset is not None or args.permission_mode is not None:
        from ghdag.llm.capabilities import PRESETS, LLMCapabilities
        if args.capabilities_preset is not None:
            capabilities = PRESETS[args.capabilities_preset]
        else:
            capabilities = LLMCapabilities()
        if args.permission_mode is not None:
            import dataclasses
            capabilities = dataclasses.replace(capabilities, permission_mode=args.permission_mode)

    call_kwargs: dict = dict(
        engine=args.engine,
        model=args.model,
        timeout=args.timeout,
        stdin_text=stdin_text,
        dangerously_skip_permissions=args.dangerously_skip_permissions,
    )
    if capabilities is not None:
        call_kwargs["capabilities"] = capabilities

    try:
        result = call(prompt, **call_kwargs)
    except EngineModelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    # 監査ログ（正常終了時のみ記録）
    audit_path = args.audit_path or os.environ.get("GHDAG_AUDIT_PATH")
    if audit_path and result.ok:
        from ghdag.llm.engines import validate_engine_model
        from ghdag.pipeline.audit import (
            compute_prompt_hash,
            write_llm_audit_log,
            write_llm_inference_audit,
        )
        resolved_model = validate_engine_model(args.engine, args.model)
        write_llm_audit_log(
            Path(audit_path),
            engine=args.engine,
            model=resolved_model,
            exit_code=result.returncode,
            correlation_id=args.correlation_id,
            timeout_sec=args.timeout,
            request_id=args.request_id,
        )
        if result.latency_ms > 0:
            write_llm_inference_audit(
                Path(audit_path),
                prompt_hash=compute_prompt_hash(prompt),
                latency_ms=result.latency_ms,
                engine=args.engine,
                model=resolved_model,
                correlation_id=args.correlation_id,
            )

    sys.exit(result.returncode)


def _cmd_cleanup(args: argparse.Namespace) -> None:
    """ghdag cleanup: queue/ のクリーンアップ。"""
    from pathlib import Path

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


def _cmd_version(args: argparse.Namespace) -> None:
    """stdout に __version__ を出力。"""
    from ghdag import __version__

    print(__version__)

