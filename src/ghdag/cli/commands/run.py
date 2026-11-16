"""ghdag run command."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ghdag.dag.hooks import DagHooks


class _HookChain:
    """Compose multiple DagHooks: forward lifecycle calls; aggregate check_* hooks."""

    def __init__(self, hooks: list[DagHooks]) -> None:
        self._hooks = hooks

    def set_engine(self, engine) -> None:
        for hook in self._hooks:
            if hasattr(hook, "set_engine"):
                hook.set_engine(engine)

    def _call_all(self, method: str, *args, **kwargs) -> None:
        for hook in self._hooks:
            fn = getattr(hook, method, None)
            if callable(fn):
                fn(*args, **kwargs)

    def on_task_start(self, uuid: str, task) -> None:
        self._call_all("on_task_start", uuid, task)

    def on_task_success(self, uuid: str, task, metrics) -> None:
        self._call_all("on_task_success", uuid, task, metrics)

    def on_task_failure(self, uuid: str, task, returncode: int, stderr_text: str, metrics) -> None:
        self._call_all("on_task_failure", uuid, task, returncode, stderr_text, metrics)

    def on_task_rejected(
        self, uuid: str, task, retry_depth: int, is_final: bool, metrics,
    ) -> None:
        self._call_all("on_task_rejected", uuid, task, retry_depth, is_final, metrics)

    def on_task_dep_failed(self, uuid: str, task, failed_dep: str) -> None:
        self._call_all("on_task_dep_failed", uuid, task, failed_dep)

    def on_task_empty_result(self, uuid: str, task, stderr_text: str, metrics) -> None:
        self._call_all("on_task_empty_result", uuid, task, stderr_text, metrics)

    def on_task_cancelled(self, uuid: str, task) -> None:
        self._call_all("on_task_cancelled", uuid, task)

    def on_task_progress(self, uuid: str, event: dict) -> None:
        self._call_all("on_task_progress", uuid, event)

    def on_shutdown(self, signum: int) -> None:
        self._call_all("on_shutdown", signum)

    def check_rejected(self, result_path: str) -> bool:
        saw_checker = False
        for hook in self._hooks:
            if hasattr(hook, "check_rejected"):
                saw_checker = True
                if hook.check_rejected(result_path):
                    return True
        if not saw_checker:
            from ghdag.dag._util import default_check_rejected
            return default_check_rejected(result_path)
        return False

    def check_pipeline_status(self, result_path: str) -> str | None:
        saw_checker = False
        for hook in self._hooks:
            if hasattr(hook, "check_pipeline_status"):
                saw_checker = True
                status = hook.check_pipeline_status(result_path)
                if status is not None:
                    return status
        if not saw_checker:
            from ghdag.dag._util import check_pipeline_status
            return check_pipeline_status(result_path)
        return None

    def check_promote_target(self, result_path: str) -> str | None:
        for hook in self._hooks:
            if hasattr(hook, "check_promote_target"):
                target = hook.check_promote_target(result_path)
                if target is not None:
                    return target
        return None


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
        task_timeout=args.task_timeout,
        quota_state_path=args.quota_state_path,
    )
    hook_modules = args.hooks
    if hook_modules:
        loaded: list[DagHooks] = [_load_hooks(module_path) for module_path in hook_modules]
        hooks: DagHooks = loaded[0] if len(loaded) == 1 else _HookChain(loaded)
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
