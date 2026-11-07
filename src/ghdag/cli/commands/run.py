"""ghdag run command."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ghdag.dag.hooks import DagHooks


def cmd_run(args) -> None:
    """Thin wrapper that builds a DagConfig and calls DagEngine.run()."""
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
        hooks = AuditHooks(audit_path=config.audit_path, tz_name=config.timezone)
    engine = DagEngine(config, hooks)
    if hooks is not None and hasattr(hooks, "set_engine"):
        hooks.set_engine(engine)
    engine.run()


def _load_hooks(module_path: str) -> DagHooks:
    """Instantiate and return a DagHooks implementation class from a module path.

    Class lookup order:
    1. If the module has a `HOOKS_CLASS` attribute, use it
    2. Otherwise, use the first public class that has `on_task_success`

    Raises:
        SystemExit: If the module or the class cannot be found
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
