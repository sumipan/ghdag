"""DagEngine — main loop, task launching, dependency resolution, append_task."""

from __future__ import annotations

import fcntl
import io
import logging
import os
import signal
import subprocess
import threading
import time

from ._util import _extract_tee_target, _stderr_reader
from .hooks import DefaultHooks, DagHooks
from .models import DagConfig, RunningTask, Task
from .parser import parse_exec_md
from .state import (
    load_done_from_dir,
    load_succeeded_from_dir,
    mark_done as state_mark_done,
)

logger = logging.getLogger(__name__)


class DagEngine:
    def __init__(self, config: DagConfig, hooks: DagHooks | None = None) -> None:
        self._config = config
        self._hooks: DagHooks = hooks or DefaultHooks()
        self._running: dict[str, RunningTask] = {}
        self._tasks: dict[str, Task] = {}
        self._shutdown = False
        self._lock_fh = None

    def run(self) -> None:
        """Main loop (blocking). Graceful shutdown on SIGINT/SIGTERM."""
        self._acquire_lock()
        self._install_signal_handlers()

        exec_md_path = str(self._config.exec_md_path)
        last_mtime = 0.0

        logger.info("DagEngine started — watching %s", exec_md_path)

        while not self._shutdown:
            # Detect exec.md changes
            try:
                mtime = os.path.getmtime(exec_md_path)
            except FileNotFoundError:
                time.sleep(self._config.poll_interval)
                continue

            if mtime != last_mtime:
                last_mtime = mtime
                task_list = parse_exec_md(exec_md_path)
                self._tasks = {t.uuid: t for t in task_list}
                logger.info("Loaded exec.md (%d tasks)", len(self._tasks))

            # Check running processes for completion
            self._check_completions()

            # Sync done state from disk
            known_done = load_done_from_dir(self._config.exec_done_dir)
            known_succeeded = load_succeeded_from_dir(self._config.exec_done_dir)

            # Propagate DEP_FAILED
            self._propagate_dep_failed(known_done, known_succeeded)

            # Launch ready tasks
            launched = 0
            for uuid, task in self._tasks.items():
                if uuid in known_done or uuid in self._running:
                    continue
                deps = set(task.depends)
                # Check if any dep failed (non-success done)
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
                    # Dependency failed — mark as DEP_FAILED
                    state_mark_done(self._config.exec_done_dir, uuid, "DEP_FAILED")
                    self._hooks.on_task_dep_failed(uuid, task, dep_failed)
                    known_done.add(uuid)
                    continue

                if not all_deps_done:
                    continue

                # All deps succeeded — launch
                if launched > 0:
                    time.sleep(self._config.launch_stagger)

                self._launch_task(uuid, task)
                launched += 1

            time.sleep(self._config.poll_interval)

        logger.info("DagEngine stopped")

    def append_task(self, line: str) -> None:
        """Append a line to exec.md with fcntl.LOCK_EX protection."""
        path = str(self._config.exec_md_path)
        with open(path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(line if line.endswith("\n") else line + "\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def mark_done(self, uuid: str, status: str | int) -> None:
        """Delegate to state.mark_done."""
        state_mark_done(self._config.exec_done_dir, uuid, status)

    # --- Internal ---

    def _acquire_lock(self) -> None:
        """Prevent multiple DagEngine instances."""
        self._lock_fh = open(str(self._config.lock_file), "w")
        try:
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.error("Another DagEngine is already running (lock: %s)", self._config.lock_file)
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

    def _launch_task(self, uuid: str, task: Task) -> None:
        logger.info("Launching [%s]: %s", uuid, task.command)
        cwd = str(self._config.cwd) if self._config.cwd else None
        proc = subprocess.Popen(
            ["bash", "-o", "pipefail", "-c", task.command],
            stderr=subprocess.PIPE,
            cwd=cwd,
        )
        buf = io.BytesIO()
        t = threading.Thread(target=_stderr_reader, args=(proc, buf), daemon=True)
        t.start()
        self._running[uuid] = RunningTask(
            uuid=uuid,
            task=task,
            proc=proc,
            started_at=time.time(),
            stderr_buf=buf,
            retry_depth=task.retry,
        )

    def _check_completions(self) -> None:
        for uuid in list(self._running):
            rt = self._running[uuid]
            if rt.proc.poll() is None:
                continue

            stderr_text = rt.stderr_buf.getvalue().decode("utf-8", errors="replace").strip()
            returncode = rt.proc.returncode
            del self._running[uuid]

            task = rt.task

            if returncode == 0:
                tee_target = _extract_tee_target(task.command)

                # Check rejected
                if tee_target and self._hooks.check_rejected(tee_target):
                    retry_depth = task.retry
                    is_final = retry_depth >= self._config.max_retry
                    if is_final:
                        state_mark_done(self._config.exec_done_dir, uuid, "REJECTED_FINAL")
                    else:
                        state_mark_done(self._config.exec_done_dir, uuid, "REJECTED")
                    self._hooks.on_task_rejected(uuid, task, retry_depth, is_final)

                # Check empty result
                elif tee_target and os.path.exists(tee_target) and os.path.getsize(tee_target) == 0:
                    state_mark_done(self._config.exec_done_dir, uuid, "EMPTY_RESULT")
                    self._hooks.on_task_empty_result(uuid, task, stderr_text)

                else:
                    state_mark_done(self._config.exec_done_dir, uuid, 0)
                    self._hooks.on_task_success(uuid, task)

            else:
                state_mark_done(self._config.exec_done_dir, uuid, returncode)
                self._hooks.on_task_failure(uuid, task, returncode, stderr_text)

    def _propagate_dep_failed(self, known_done: set[str], known_succeeded: set[str]) -> None:
        """Mark tasks whose dependencies have failed as DEP_FAILED."""
        changed = True
        while changed:
            changed = False
            for uuid, task in self._tasks.items():
                if uuid in known_done or uuid in self._running:
                    continue
                for dep in task.depends:
                    if dep in known_done and dep not in known_succeeded:
                        state_mark_done(self._config.exec_done_dir, uuid, "DEP_FAILED")
                        self._hooks.on_task_dep_failed(uuid, task, dep)
                        known_done.add(uuid)
                        changed = True
                        break
