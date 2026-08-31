"""DagEngine — main loop, task launching, dependency resolution, append_task."""

from __future__ import annotations

import fcntl
import logging
import os
import signal
import time
from pathlib import Path

from ghdag.core.vocabulary import DONE_DEP_FAILED

from .circuit_breaker import CircuitBreakerPolicy
from .fanout_manager import FanOutManager
from .hooks import DagHooks, DefaultHooks
from .models import DagConfig, Task
from .parser import parse_jsonl, validate_dependencies
from .state import (
    load_done_from_dir,
    load_succeeded_from_dir,
)
from .state import (
    mark_done as state_mark_done,
)
from .task_launcher import TaskLauncher

logger = logging.getLogger(__name__)


class DagEngine:
    def __init__(self, config: DagConfig, hooks: DagHooks | None = None) -> None:
        self._config = config
        if hooks is None:
            self._hooks: DagHooks = DefaultHooks()
        else:
            self._hooks = hooks
        self._tasks: dict[str, Task] = {}
        self._shutdown = False
        self._lock_fh = None

        self._circuit_breaker = CircuitBreakerPolicy(
            failure_window_sec=config.failure_window_sec,
            max_consecutive_failures=config.max_consecutive_failures,
        )
        self._fanout_manager = FanOutManager(
            config, self._hooks, self.append_task, self._run_promote
        )
        self._launcher = TaskLauncher(
            config, self._hooks, self._circuit_breaker,
            self._fanout_manager, self._run_promote,
        )

    def run(self) -> None:
        """Main loop (blocking). Graceful shutdown on SIGINT/SIGTERM."""
        self._acquire_lock()
        self._install_signal_handlers()

        exec_jsonl_path = str(self._config.exec_jsonl_path)
        last_mtime = 0.0

        logger.info("DagEngine started — watching %s", exec_jsonl_path)

        while not self._shutdown:
            try:
                mtime = os.path.getmtime(exec_jsonl_path)
            except FileNotFoundError:
                time.sleep(self._config.poll_interval)
                continue

            if mtime != last_mtime:
                last_mtime = mtime
                with open(exec_jsonl_path, encoding="utf-8") as f:
                    fcntl.flock(f, fcntl.LOCK_SH)
                    try:
                        text = f.read()
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
                task_list = parse_jsonl(text)
                self._tasks = {t.uuid: t for t in task_list}
                logger.info("Loaded exec file (%d tasks)", len(self._tasks))

            self._launcher.check_completions()

            if self._circuit_breaker.tripped:
                self._shutdown = True
                break

            known_done = load_done_from_dir(self._config.exec_done_dir)
            known_succeeded = load_succeeded_from_dir(self._config.exec_done_dir)

            self._fanout_manager.check_completions(known_done, known_succeeded)

            invalid_tasks = validate_dependencies(list(self._tasks.values()), known_done)
            for inv_uuid, reason in invalid_tasks.items():
                if inv_uuid not in known_done and not self._launcher.is_running(inv_uuid):
                    state_mark_done(self._config.exec_done_dir, inv_uuid, DONE_DEP_FAILED)
                    self._hooks.on_task_dep_failed(inv_uuid, self._tasks[inv_uuid], reason)
                    known_done.add(inv_uuid)

            self._propagate_dep_failed(known_done, known_succeeded)

            launched = 0
            for uuid, task in self._tasks.items():
                if (
                    uuid in known_done
                    or self._launcher.is_running(uuid)
                    or self._fanout_manager.is_pending(uuid)
                ):
                    continue
                deps = set(task.depends)
                dep_failed = None
                all_deps_done = True
                for dep in deps:
                    if dep not in known_done:
                        all_deps_done = False
                        break
                    if dep not in known_succeeded:
                        dep_failed = dep
                        break

                if dep_failed is not None:
                    state_mark_done(self._config.exec_done_dir, uuid, DONE_DEP_FAILED)
                    self._hooks.on_task_dep_failed(uuid, task, dep_failed)
                    known_done.add(uuid)
                    continue

                if not all_deps_done:
                    continue

                if (
                    self._config.max_concurrency is not None
                    and self._launcher.running_count >= self._config.max_concurrency
                ):
                    continue

                if self._config.serialize_mutating:
                    task_is_mutating = task.annotations.get("_mutates") == "true"
                    running_has_mutating = any(
                        rt.task.annotations.get("_mutates") == "true"
                        for rt in self._running.values()
                    )
                    if task_is_mutating and running_has_mutating:
                        continue

                if launched > 0:
                    time.sleep(self._config.launch_stagger)

                self._launcher.launch(uuid, task)
                launched += 1

            time.sleep(self._config.poll_interval)

        logger.info("DagEngine stopped")

    def append_task(self, line: str) -> None:
        """Append a line to exec.jsonl with fcntl.LOCK_EX protection."""
        path = str(self._config.exec_jsonl_path)
        with open(path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(line if line.endswith("\n") else line + "\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def mark_done(self, uuid: str, status: str | int) -> None:
        """Delegate to state.mark_done."""
        state_mark_done(self._config.exec_done_dir, uuid, status)

    @property
    def _running(self):
        return self._launcher._running

    # --- Internal ---

    def _acquire_lock(self) -> None:
        """Prevent multiple DagEngine instances."""
        self._lock_fh = open(str(self._config.lock_file), "w")
        try:
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.error(
                "Another DagEngine is already running (lock: %s)", self._config.lock_file
            )
            raise

    def _install_signal_handlers(self) -> None:
        import threading

        if threading.current_thread() is not threading.main_thread():
            logger.debug("Skipping signal handler install (not main thread)")
            return

        def _handler(signum, frame):
            self._shutdown = True
            self._hooks.on_shutdown(signum)

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)

    def _run_promote(self, result_path: str | None) -> None:
        if not result_path:
            return
        promote_target = self._hooks.check_promote_target(result_path)
        if not promote_target:
            return
        from ghdag.files import md_promote
        try:
            md_promote(
                result_path,
                promote_target,
                repo_root=Path(self._config.exec_jsonl_path).parent,
            )
        except Exception:
            logger.warning(
                "Promote failed for %s → %s", result_path, promote_target, exc_info=True
            )

    def _propagate_dep_failed(self, known_done: set[str], known_succeeded: set[str]) -> None:
        """Mark tasks whose dependencies have failed as DEP_FAILED."""
        changed = True
        while changed:
            changed = False
            for uuid, task in self._tasks.items():
                if uuid in known_done or self._launcher.is_running(uuid):
                    continue
                for dep in task.depends:
                    if dep in known_done and dep not in known_succeeded:
                        state_mark_done(self._config.exec_done_dir, uuid, DONE_DEP_FAILED)
                        self._hooks.on_task_dep_failed(uuid, task, dep)
                        known_done.add(uuid)
                        changed = True
                        break
