"""ghdag run command."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ghdag.config.env import resolve_runtime_paths, run_recorder_enabled
from ghdag.core.exceptions import GhdagError

if TYPE_CHECKING:
    from ghdag.core.models.metrics import TaskMetrics
    from ghdag.dag.hooks import DagHooks
    from ghdag.dag.models import Task


class _RunRecorder:
    """Private audit + metrics recorder for ``ghdag run`` (issue sumipan/ghdag#8060)."""

    def __init__(
        self,
        audit_path: Path,
        metrics_path: Path,
        *,
        tz_name: str | None,
    ) -> None:
        from ghdag.dag.audit_hooks import AuditHooks
        from ghdag.metrics.recorder import MetricsRecorder

        self._audit = AuditHooks(audit_path=audit_path, tz_name=tz_name)
        self._metrics = MetricsRecorder(metrics_path, tz_name=tz_name or "UTC")

    def on_task_start(self, uuid: str, task: Task) -> None:
        self._audit.on_task_start(uuid, task)

    def on_task_success(self, uuid: str, task: Task, metrics: TaskMetrics) -> None:
        self._audit.on_task_success(uuid, task, metrics)
        self._metrics.record(metrics)

    def on_task_failure(
        self,
        uuid: str,
        task: Task,
        returncode: int,
        stderr_text: str,
        metrics: TaskMetrics,
    ) -> None:
        self._audit.on_task_failure(uuid, task, returncode, stderr_text, metrics)
        self._metrics.record(metrics)

    def on_task_rejected(
        self,
        uuid: str,
        task: Task,
        retry_depth: int,
        is_final: bool,
        metrics: TaskMetrics,
    ) -> None:
        self._audit.on_task_rejected(uuid, task, retry_depth, is_final, metrics)
        self._metrics.record(metrics)

    def on_task_dep_failed(self, uuid: str, task: Task, failed_dep: str) -> None:
        self._audit.on_task_dep_failed(uuid, task, failed_dep)

    def on_task_empty_result(
        self,
        uuid: str,
        task: Task,
        stderr_text: str,
        metrics: TaskMetrics,
    ) -> None:
        self._audit.on_task_empty_result(uuid, task, stderr_text, metrics)
        self._metrics.record(metrics)

    def on_task_cancelled(self, uuid: str, task: Task) -> None:
        self._audit.on_task_cancelled(uuid, task)

    def on_task_progress(self, uuid: str, event: dict) -> None:
        self._audit.on_task_progress(uuid, event)

    def on_shutdown(self, signum: int) -> None:
        self._audit.on_shutdown(signum)


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
    """Thin wrapper that builds a DagConfig and calls DagEngine.run().

    When the built-in recorder is enabled (see ``run_recorder_enabled``), host consumers
    that still pass ``--hooks`` must set ``GHDAG_RUN_RECORDER=0`` before their ghdag pin
    catches this release; ``sumipan/nexus#8059`` (commit ``6a08e38``) pre-migrated Procfile.
    """
    if not os.path.exists(args.exec_jsonl):
        raise GhdagError(
            f"file not found: {args.exec_jsonl}",
            code="EXEC_JSONL_NOT_FOUND",
        )

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
        nodes_path=getattr(args, "nodes", None),
    )
    hook_modules = args.hooks
    if run_recorder_enabled():
        runtime = resolve_runtime_paths(args.exec_jsonl)
        recorder = _RunRecorder(
            cast(Path, config.audit_path),
            runtime.state_dir / "metrics.jsonl",
            tz_name=config.timezone,
        )
        recorder_hook = cast("DagHooks", recorder)
        if hook_modules:
            loaded: list[DagHooks] = [
                _load_hooks(module_path) for module_path in hook_modules
            ]
            hooks: DagHooks = _HookChain([recorder_hook, *loaded])
        else:
            hooks = _HookChain([recorder_hook])
    elif hook_modules:
        loaded = [_load_hooks(module_path) for module_path in hook_modules]
        hooks = loaded[0] if len(loaded) == 1 else _HookChain(loaded)
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
        GhdagError: If the module or the class cannot be found
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
        raise GhdagError(
            f"cannot import hooks module '{module_path}': {exc}",
            code="HOOKS_IMPORT_FAILED",
        ) from exc

    if hasattr(module, "HOOKS_CLASS"):
        cls = module.HOOKS_CLASS
        return cast(DagHooks, cls())

    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ == module.__name__ and hasattr(obj, "on_task_success"):
            return cast(DagHooks, obj())

    raise GhdagError(
        f"no DagHooks-compatible class found in module '{module_path}'",
        code="HOOKS_CLASS_NOT_FOUND",
    )


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    run_parser = subparsers.add_parser(
        "run", allow_abbrev=False, help="Run exec.jsonl via DagEngine",
    )
    run_parser.add_argument("exec_jsonl", help="Path to exec.jsonl file")
    run_parser.add_argument(
        "--interval-sec",
        type=float,
        default=1.0,
        dest="interval",
        metavar="SEC",
        help="Poll interval in seconds (default: 1.0)",
    )
    run_parser.add_argument(
        "--task-timeout",
        type=float,
        default=None,
        dest="task_timeout",
        metavar="SEC",
        help="Per-task timeout in seconds (default: no limit)",
    )
    run_parser.add_argument(
        "--quota-state-path",
        default=None,
        dest="quota_state_path",
        metavar="PATH",
        help=(
            "Path to quota gate state JSON "
            "(default: $GHDAG_STATE_DIR/quota-gate.json or <exec.jsonl parent>/quota-gate.json)"
        ),
    )
    run_parser.add_argument(
        "--hooks",
        action="append",
        default=None,
        metavar="MODULE",
        help="Python module path for DagHooks implementation (repeatable)",
    )
    run_parser.add_argument(
        "--max-concurrency",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of concurrent tasks (default: unlimited)",
    )
    run_parser.add_argument(
        "--nodes",
        default=None,
        dest="nodes",
        metavar="PATH",
        help="YAML file listing remote SSH execution nodes",
    )
    run_parser.set_defaults(func=cmd_run)
    return cast(argparse.ArgumentParser, run_parser)
