"""ghdag run コマンド。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ghdag.dag.hooks import DagHooks


def cmd_run(args) -> None:
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
        from ghdag.dag.audit_hooks import AuditHooks
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
