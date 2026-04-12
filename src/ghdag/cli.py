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
    run_parser.add_argument(
        "--hooks",
        default=None,
        metavar="MODULE",
        help="Python module path for DagHooks implementation (e.g. scripts.diary_hooks)",
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

    # ghdag shr
    shr_parser = subparsers.add_parser("shr", help="Self-hosted runner management")
    shr_sub = shr_parser.add_subparsers(title="shr commands")

    # ghdag shr init
    init_p = shr_sub.add_parser("init", help="Download, configure, and register runner")
    init_p.add_argument("--repo", required=True, help="owner/repo")
    init_p.add_argument("--labels", required=True, help="Comma-separated labels")
    init_p.set_defaults(func=_cmd_shr_init)

    # ghdag shr start / stop / status / teardown
    for name, help_text, func in [
        ("start", "Start runner via launchd", _cmd_shr_start),
        ("stop", "Stop runner via launchd", _cmd_shr_stop),
        ("status", "Show runner status", _cmd_shr_status),
        ("teardown", "Unregister and clean up runner", _cmd_shr_teardown),
    ]:
        p = shr_sub.add_parser(name, help=help_text)
        p.set_defaults(func=func)

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
    hooks = _load_hooks(args.hooks) if args.hooks else None
    engine = DagEngine(config, hooks)
    if hooks is not None and hasattr(hooks, "set_engine"):
        hooks.set_engine(engine)
    engine.run()


def _load_hooks(module_path: str) -> object:
    """モジュールパスから DagHooks 実装クラスをインスタンス化して返す。

    クラスの探索順:
    1. モジュールに `HOOKS_CLASS` 属性がある場合はそれを使用
    2. `on_task_success` を持つ最初の公開クラスを使用

    Raises:
        SystemExit: モジュールが見つからない、またはクラスが見つからない場合
    """
    import importlib
    import inspect

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
        return cls()

    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ == module.__name__ and hasattr(obj, "on_task_success"):
            return obj()

    print(
        f"error: no DagHooks-compatible class found in module '{module_path}'",
        file=sys.stderr,
    )
    sys.exit(1)


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


# ---------------------------------------------------------------------------
# shr subcommands
# ---------------------------------------------------------------------------

def _cmd_shr_init(args: argparse.Namespace) -> None:
    """ghdag shr init: runner DL → config → launchd 登録 → shr.json 保存。"""
    import shutil
    from ghdag.shr import config as shr_config
    from ghdag.shr import github as shr_github
    from ghdag.shr import runner as shr_runner
    from ghdag.shr import daemon as shr_daemon
    from ghdag.shr.config import ShrConfig

    if shr_config.CONFIG_PATH.exists():
        print(
            "エラー: 既に初期化済みです。teardown してから再実行してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    labels = [label.strip() for label in args.labels.split(",")]

    # 1. 登録トークン取得
    try:
        token = shr_github.get_registration_token(args.repo)
    except Exception as exc:
        print(f"エラー: runner 登録トークンの取得に失敗しました: {exc}", file=sys.stderr)
        sys.exit(1)

    # 2. runner DL・展開
    runner_dir = shr_config.RUNNER_DIR
    try:
        shr_runner.download_runner(runner_dir)
    except Exception as exc:
        print(f"エラー: actions-runner のダウンロードに失敗しました: {exc}", file=sys.stderr)
        sys.exit(1)

    # 3. config.sh 実行
    try:
        shr_runner.configure_runner(runner_dir, args.repo, token, labels)
    except Exception as exc:
        print(f"エラー: runner の設定に失敗しました: {exc}", file=sys.stderr)
        shutil.rmtree(runner_dir, ignore_errors=True)
        sys.exit(1)

    # 4. launchd plist 生成・配置
    try:
        plist_path = shr_daemon.install_plist(runner_dir, "com.ghdag.runner")
    except Exception as exc:
        print(f"エラー: launchd plist の配置に失敗しました: {exc}", file=sys.stderr)
        shutil.rmtree(runner_dir, ignore_errors=True)
        sys.exit(1)

    # 5. shr.json 保存
    cfg = ShrConfig(
        repo=args.repo,
        labels=labels,
        runner_dir=str(runner_dir),
        plist_path=str(plist_path),
    )
    shr_config.save_config(cfg)
    print(f"runner を初期化しました: {args.repo} (labels: {labels})")


def _cmd_shr_start(args: argparse.Namespace) -> None:
    """ghdag shr start: launchctl load で runner を起動する。"""
    from pathlib import Path
    from ghdag.shr import config as shr_config
    from ghdag.shr import daemon as shr_daemon

    try:
        cfg = shr_config.load_config()
    except FileNotFoundError:
        print("エラー: init を先に実行してください。", file=sys.stderr)
        sys.exit(1)

    plist_path = Path(cfg.plist_path)
    if shr_daemon.is_loaded("com.ghdag.runner"):
        print("既に起動中です。")
        return

    shr_daemon.load(plist_path)
    print("runner を起動しました。")


def _cmd_shr_stop(args: argparse.Namespace) -> None:
    """ghdag shr stop: launchctl unload で runner を停止する。"""
    from pathlib import Path
    from ghdag.shr import config as shr_config
    from ghdag.shr import daemon as shr_daemon

    try:
        cfg = shr_config.load_config()
    except FileNotFoundError:
        print("エラー: init を先に実行してください。", file=sys.stderr)
        sys.exit(1)

    plist_path = Path(cfg.plist_path)
    if not shr_daemon.is_loaded("com.ghdag.runner"):
        print("停止済みです。")
        return

    shr_daemon.unload(plist_path)
    print("runner を停止しました。")


def _cmd_shr_status(args: argparse.Namespace) -> None:
    """ghdag shr status: ローカル + GitHub の runner 状態を表示する。"""
    from ghdag.shr import config as shr_config
    from ghdag.shr import daemon as shr_daemon
    from ghdag.shr import github as shr_github

    try:
        cfg = shr_config.load_config()
    except FileNotFoundError:
        print("エラー: init を先に実行してください。", file=sys.stderr)
        sys.exit(1)

    local_status = "起動中" if shr_daemon.is_loaded("com.ghdag.runner") else "停止中"

    try:
        import socket
        runner_name = socket.gethostname()
        github_status = shr_github.get_runner_status(cfg.repo, runner_name)
    except Exception:
        github_status = "確認失敗"

    print(f"ローカル: {local_status} / GitHub: {github_status}")


def _cmd_shr_teardown(args: argparse.Namespace) -> None:
    """ghdag shr teardown: 登録解除 → plist 削除 → runner 削除 → shr.json 削除。"""
    from pathlib import Path
    from ghdag.shr import config as shr_config
    from ghdag.shr import daemon as shr_daemon
    from ghdag.shr import github as shr_github
    from ghdag.shr import runner as shr_runner
    import shutil

    try:
        cfg = shr_config.load_config()
    except FileNotFoundError:
        print("エラー: init を先に実行してください。", file=sys.stderr)
        sys.exit(1)

    runner_dir = Path(cfg.runner_dir)
    plist_path = Path(cfg.plist_path)

    # 起動中なら先に停止
    if shr_daemon.is_loaded("com.ghdag.runner"):
        shr_daemon.unload(plist_path)

    # remove トークン取得（失敗したらローカルファイルは残す）
    try:
        remove_token = shr_github.get_removal_token(cfg.repo)
    except Exception as exc:
        print(f"エラー: remove トークンの取得に失敗しました: {exc}", file=sys.stderr)
        sys.exit(1)

    # runner 登録解除
    try:
        shr_runner.remove_runner(runner_dir, remove_token)
    except Exception as exc:
        print(f"エラー: runner の登録解除に失敗しました: {exc}", file=sys.stderr)
        sys.exit(1)

    # plist 削除・runner ディレクトリ削除・shr.json 削除
    shr_daemon.uninstall_plist(plist_path)
    shutil.rmtree(runner_dir, ignore_errors=True)
    shr_config.CONFIG_PATH.unlink(missing_ok=True)
    print("runner を削除しました。")
