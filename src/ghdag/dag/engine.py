"""DagEngine — main loop, task launching, dependency resolution, append_task."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import IO

from ghdag.core.vocabulary import DONE_DEFERRED, DONE_DEP_FAILED
from ghdag.io import exec_jsonl
from ghdag.io.audit import AuditContext
from ghdag.io.done import read_done_content
from ghdag.quota import QuotaGate

from .circuit_breaker import CircuitBreakerPolicy
from .fanout_manager import FanOutManager
from .hooks import DagHooks, DefaultHooks
from .models import DagConfig, Task
from .parser import validate_dependencies
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
        self._draining = False
        self._drain_deadline: float | None = None
        self._lock_fh: IO[str] | None = None
        self._adopt_done = False

        self._circuit_breaker = CircuitBreakerPolicy(
            failure_window_sec=config.failure_window_sec,
            max_consecutive_failures=config.max_consecutive_failures,
        )
        self._fanout_manager = FanOutManager(
            config, self._hooks, self._append_fanout_child, self._run_promote
        )
        quota_state_path = config.quota_state_path
        if quota_state_path is None:
            raise ValueError("DagConfig.quota_state_path must be set")
        self._launcher = TaskLauncher(
            config, self._hooks, self._circuit_breaker,
            self._fanout_manager, self._run_promote,
            quota_gate=QuotaGate(quota_state_path, audit_path=config.quota_audit_path),
        )
        self._quota_gate = self._launcher.quota_gate

    def run(self) -> None:
        """Main loop (blocking). Graceful shutdown on SIGINT/SIGTERM."""
        self._acquire_lock()
        self._install_signal_handlers()

        exec_jsonl_path = str(self._config.exec_jsonl_path)
        last_mtime = 0.0

        logger.info("DagEngine started — watching %s", exec_jsonl_path)

        while not self._shutdown:
            if self._draining:
                self._apply_pending_cancels()
                self._launcher.check_completions()
                if self._drain_deadline is not None and time.monotonic() >= self._drain_deadline:
                    self._launcher.terminate_all()
                if self._launcher.running_count == 0:
                    break
                time.sleep(self._config.poll_interval)
                continue

            try:
                mtime = os.path.getmtime(exec_jsonl_path)
            except FileNotFoundError:
                time.sleep(self._config.poll_interval)
                continue

            if mtime != last_mtime:
                last_mtime = mtime
                text = exec_jsonl.read(Path(exec_jsonl_path))
                task_list = exec_jsonl.parse(text)
                self._tasks = {t.uuid: t for t in task_list}
                logger.info("Loaded exec file (%d tasks)", len(self._tasks))
                if not self._adopt_done:
                    self._adopt_done = True
                    self._launcher.adopt_orphans(self._tasks)

            self._apply_pending_cancels()
            self._launcher.check_completions()
            try:
                released_uuids = self._quota_gate.release_ready()
            except ValueError:
                logger.exception("Quota state is unreadable; skipping launches in this poll")
                time.sleep(self._config.poll_interval)
                continue
            self._requeue_deferred(released_uuids)

            if self._circuit_breaker.tripped:
                self._shutdown = True
                break

            known_done = load_done_from_dir(self._config.exec_done_dir)
            known_deferred = {
                uuid for uuid in known_done
                if read_done_content(Path(self._config.exec_done_dir), uuid) == DONE_DEFERRED
            }
            known_succeeded = load_succeeded_from_dir(self._config.exec_done_dir)

            self._fanout_manager.check_completions(known_done, known_succeeded)

            invalid_tasks = validate_dependencies(
                list(self._tasks.values()), known_done - known_deferred
            )
            for inv_uuid, reason in invalid_tasks.items():
                if inv_uuid not in known_done and not self._launcher.is_running(inv_uuid):
                    state_mark_done(self._config.exec_done_dir, inv_uuid, DONE_DEP_FAILED)
                    self._hooks.on_task_dep_failed(inv_uuid, self._tasks[inv_uuid], reason)
                    known_done.add(inv_uuid)

            self._propagate_dep_failed(known_done, known_succeeded, known_deferred)

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
                    if dep not in known_done or dep in known_deferred:
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

                if self._launcher.launch(uuid, task):
                    launched += 1

            time.sleep(self._config.poll_interval)

        logger.info("DagEngine stopped")

    def append_task(self, line: str, audit_context: AuditContext | None = None) -> None:
        """Append a JSONL record to exec.jsonl with LOCK_EX + audit enqueue."""
        path = Path(self._config.exec_jsonl_path)
        record = json.loads(line if not line.endswith("\n") else line[:-1])
        exec_jsonl.append(
            path,
            [record],
            audit_context or AuditContext(source="dag"),
            audit_path=path.parent / "audit.jsonl",
            quota_gate=self._quota_gate,
        )

    def _append_fanout_child(self, line: str, parent_uuid: str) -> None:
        """FanOutManager callback: build AuditContext and append child task."""
        self.append_task(
            line,
            AuditContext(source="fanout", correlation_id=parent_uuid),
        )

    def mark_done(self, uuid: str, status: str | int) -> None:
        """Delegate to state.mark_done."""
        state_mark_done(self._config.exec_done_dir, uuid, status)

    @property
    def _running(self):
        return self._launcher._running

    # --- Internal ---

    def _apply_pending_cancels(self) -> None:
        """Detect jobs/cancel/<uuid> for running tasks and ask the launcher to cancel."""
        cancel_dir = Path(self._config.exec_done_dir).parent / "cancel"
        if not cancel_dir.is_dir():
            return
        for uuid in list(self._launcher._running):
            if (cancel_dir / uuid).exists():
                self._launcher.request_cancel(uuid)

    def _acquire_lock(self) -> None:
        """Prevent multiple DagEngine instances."""
        lock_fh = open(str(self._config.lock_file), "w")
        self._lock_fh = lock_fh
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
            if self._draining:
                self._shutdown = True
                return
            self._launcher.mark_interrupted_all()
            self._draining = True
            # task_timeout is None: no drain; terminate right away (interrupt is recorded).
            self._drain_deadline = time.monotonic() + (self._config.task_timeout or 0)
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

    def _requeue_deferred(self, released_uuids: list[str]) -> None:
        """Remove DONE_DEFERRED done files so released tasks can be re-launched."""
        done_dir = self._config.exec_done_dir
        for uuid in released_uuids:
            content = read_done_content(Path(done_dir), uuid)
            if content is not None and content.strip() == DONE_DEFERRED:
                done_file = Path(done_dir) / uuid
                try:
                    done_file.unlink()
                    logger.info("Task [%s] requeued after deferred release", uuid)
                except OSError:
                    logger.warning("Failed to remove done file for requeued task [%s]", uuid)

    def _propagate_dep_failed(
        self,
        known_done: set[str],
        known_succeeded: set[str],
        known_deferred: set[str] | None = None,
    ) -> None:
        """Mark tasks whose dependencies have failed as DEP_FAILED."""
        _deferred = known_deferred or set()
        changed = True
        while changed:
            changed = False
            for uuid, task in self._tasks.items():
                if uuid in known_done or self._launcher.is_running(uuid):
                    continue
                for dep in task.depends:
                    if dep in _deferred:
                        continue
                    if dep in known_done and dep not in known_succeeded:
                        state_mark_done(self._config.exec_done_dir, uuid, DONE_DEP_FAILED)
                        self._hooks.on_task_dep_failed(uuid, task, dep)
                        known_done.add(uuid)
                        changed = True
                        break
