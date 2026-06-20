"""ghdag ui コマンド。"""

from __future__ import annotations

from pathlib import Path


def cmd_ui(args) -> None:
    """ghdag ui: Web UI ダッシュボードを起動する。"""
    from ghdag.ui.server import run_server

    repo_root = Path(args.repo_root).resolve()

    run_server(
        repo_root=repo_root,
        host=args.host,
        port=args.port,
        poll_interval=args.interval,
        max_visible=args.max_visible,
    )
