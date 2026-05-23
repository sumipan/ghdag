"""DagEngine — main loop, task launching, dependency resolution, append_task."""

from __future__ import annotations

import fcntl
import io
import logging
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

from ._util import _extract_tee_target, _stderr_reader, _stdout_reader
from .fanout import FanOutSpec, build_child_exec_line, build_child_jsonl_record, parse_fanout_spec
from .hooks import DefaultHooks, DagHooks
from .models import DagConfig, RunningTask, Task
from .parser import parse_exec_md, parse_jsonl, validate_dependencies
from .state import (
    load_done_from_dir,
    load_succeeded_from_dir,
    mark_done as state_mark_done,
)
from ghdag.metrics.models import FailureClass, TaskMetrics
from ghdag.metrics.parsers import parse_engine_model, parse_token_count

logger = logging.getLogger(__name__)

_STDIN_REDIR_RE = re.compile(r"(?<!<)<\s+(\S+)")


class DagEngine:
    def __init__(self, config: DagConfig, hooks: DagHooks | None = None) -> None:
        self._config = config
        if hooks is None:
            audit_path = Path(config.exec_md_path).parent / "audit.jsonl"
            self._hooks: DagHooks = DefaultHooks(audit_path=audit_path)
        else:
            self._hooks = hooks
        self._running: dict[str, RunningTask] = {}
        self._tasks: dict[str, Task] = {}
        self._shutdown = False
        self._lock_fh = None
        self._fanout_pending: dict[str, set[str]] = {}
        self._fanout_tasks: dict[str, Task] = {}
        self._fanout_metrics: dict[str, TaskMetrics] = {}

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
                if exec_md_path.endswith(".jsonl"):
                    with open(exec_md_path, encoding="utf-8") as f:
                        fcntl.flock(f, fcntl.LOCK_SH)
                        try:
                            text = f.read()
                        finally:
                            fcntl.flock(f, fcntl.LOCK_UN)
                    task_list = parse_jsonl(text)
                else:
                    task_list = parse_exec_md(exec_md_path)
                self._tasks = {t.uuid: t for t in task_list}
                logger.info("Loaded exec.md (%d tasks)", len(self._tasks))

            # Check running processes for completion (includes timeout enforcement)
            self._check_completions()

            # Sync done state from disk
            known_done = load_done_from_dir(self._config.exec_done_dir)
            known_succeeded = load_succeeded_from_dir(self._config.exec_done_dir)

            # Join fan-out parents whose children have all completed
            self._check_fanout_completions(known_done, known_succeeded)

            # Validate dependency graph and mark invalid tasks immediately
            invalid_tasks = validate_dependencies(list(self._tasks.values()), known_done)
            for inv_uuid, reason in invalid_tasks.items():
                if inv_uuid not in known_done and inv_uuid not in self._running:
                    state_mark_done(self._config.exec_done_dir, inv_uuid, "DEP_FAILED")
                    self._hooks.on_task_dep_failed(inv_uuid, self._tasks[inv_uuid], reason)
                    known_done.add(inv_uuid)

            # Propagate DEP_FAILED
            self._propagate_dep_failed(known_done, known_succeeded)

            # Launch ready tasks
            launched = 0
            for uuid, task in self._tasks.items():
                if uuid in known_done or uuid in self._running or uuid in self._fanout_pending:
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

        m = _STDIN_REDIR_RE.search(task.command)
        if m:
            input_file = m.group(1)
            base = Path(cwd) if cwd else Path()
            check_path = Path(input_file) if Path(input_file).is_absolute() else base / input_file
            if not check_path.exists():
                logger.warning(
                    "Task [%s] skipped — stdin input file missing: %s (command: %s)",
                    uuid, input_file, task.command,
                )
                state_mark_done(self._config.exec_done_dir, uuid, "SKIPPED_MISSING_INPUT")
                return

        stdout_buf: io.BytesIO | None = None
        t_stdout: threading.Thread | None = None
        if task.result_path is not None:
            proc = subprocess.Popen(
                ["bash", "-o", "pipefail", "-c", task.command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
            )
            stdout_buf = io.BytesIO()
            t_stdout = threading.Thread(target=_stdout_reader, args=(proc, stdout_buf), daemon=True)
            t_stdout.start()
        else:
            proc = subprocess.Popen(
                ["bash", "-o", "pipefail", "-c", task.command],
                stderr=subprocess.PIPE,
                cwd=cwd,
            )

        stderr_buf = io.BytesIO()
        t_stderr = threading.Thread(target=_stderr_reader, args=(proc, stderr_buf), daemon=True)
        t_stderr.start()
        self._running[uuid] = RunningTask(
            uuid=uuid,
            task=task,
            proc=proc,
            started_at=time.time(),
            started_at_mono=time.monotonic(),
            stderr_buf=stderr_buf,
            retry_depth=task.retry,
            stdout_buf=stdout_buf,
            stderr_thread=t_stderr,
            stdout_thread=t_stdout,
        )
        self._hooks.on_task_start(uuid, task)

    def _check_completions(self) -> None:
        for uuid in list(self._running):
            rt = self._running[uuid]

            # Wall-clock timeout enforcement
            task_timeout = self._config.task_timeout
            if task_timeout is not None and rt.proc.poll() is None:
                now = time.monotonic()
                if rt.term_sent_at is None and (now - rt.started_at_mono) > task_timeout:
                    logger.warning("Task [%s] exceeded timeout %.1fs, sending SIGTERM", uuid, task_timeout)
                    rt.proc.terminate()
                    rt.term_sent_at = now
                elif rt.term_sent_at is not None and (now - rt.term_sent_at) > self._config.kill_grace:
                    logger.warning("Task [%s] still alive after grace period, sending SIGKILL", uuid)
                    rt.proc.kill()

            if rt.proc.poll() is None:
                continue

            was_timeout = rt.term_sent_at is not None
            finished_at = time.time()
            del self._running[uuid]
            self._join_reader_threads(rt)
            stderr_text = rt.stderr_buf.getvalue().decode("utf-8", errors="replace").strip()
            returncode = rt.proc.returncode

            task = rt.task
            engine = task.engine
            model = task.model
            if engine is None:
                engine, model = parse_engine_model(task.command)
            token_count = parse_token_count(engine, stderr_text)

            try:
                if was_timeout:
                    state_mark_done(self._config.exec_done_dir, uuid, "TIMEOUT")
                    metrics = TaskMetrics(
                        uuid=uuid, engine=engine, model=model,
                        wall_time_sec=round(finished_at - rt.started_at, 3),
                        token_count=token_count, status="failure",
                        started_at=rt.started_at, finished_at=finished_at,
                        correlation_id=task.idempotency_key,
                        failure_class=FailureClass.TIMEOUT,
                    )
                    timeout_msg = f"TIMEOUT: task exceeded task_timeout={self._config.task_timeout}s"
                    self._hooks.on_task_failure(uuid, task, returncode, timeout_msg, metrics)
                    continue

                if returncode == 0:
                    if task.result_path is not None:
                        stdout_data = rt.stdout_buf.getvalue() if rt.stdout_buf else b""
                        Path(task.result_path).write_bytes(stdout_data)
                        effective_result_path: str | None = task.result_path
                    else:
                        effective_result_path = _extract_tee_target(task.command)

                    # Check rejected
                    if effective_result_path and self._hooks.check_rejected(effective_result_path):
                        retry_depth = task.retry
                        is_final = retry_depth >= self._config.max_retry
                        if is_final:
                            state_mark_done(self._config.exec_done_dir, uuid, "REJECTED_FINAL")
                        else:
                            state_mark_done(self._config.exec_done_dir, uuid, "REJECTED")
                        metrics = TaskMetrics(
                            uuid=uuid, engine=engine, model=model,
                            wall_time_sec=round(finished_at - rt.started_at, 3),
                            token_count=token_count, status="rejected",
                            started_at=rt.started_at, finished_at=finished_at,
                            correlation_id=task.idempotency_key,
                            failure_class=FailureClass.REJECTED,
                        )
                        self._hooks.on_task_rejected(uuid, task, retry_depth, is_final, metrics)

                    # Check PIPELINE_STATUS: *_FAILED
                    elif effective_result_path and (pipeline_status := self._hooks.check_pipeline_status(effective_result_path)) and pipeline_status.endswith("_FAILED"):
                        state_mark_done(self._config.exec_done_dir, uuid, f"PIPELINE_FAILED:{pipeline_status}")
                        metrics = TaskMetrics(
                            uuid=uuid, engine=engine, model=model,
                            wall_time_sec=round(finished_at - rt.started_at, 3),
                            token_count=token_count, status="failure",
                            started_at=rt.started_at, finished_at=finished_at,
                            correlation_id=task.idempotency_key,
                            failure_class=FailureClass.PIPELINE_FAILED,
                        )
                        self._hooks.on_task_failure(uuid, task, 0, f"PIPELINE_FAILED:{pipeline_status}", metrics)

                    # Check empty result
                    elif effective_result_path and os.path.exists(effective_result_path) and os.path.getsize(effective_result_path) == 0:
                        state_mark_done(self._config.exec_done_dir, uuid, "EMPTY_RESULT")
                        metrics = TaskMetrics(
                            uuid=uuid, engine=engine, model=model,
                            wall_time_sec=round(finished_at - rt.started_at, 3),
                            token_count=token_count, status="empty_result",
                            started_at=rt.started_at, finished_at=finished_at,
                            correlation_id=task.idempotency_key,
                            failure_class=FailureClass.EMPTY_RESULT,
                        )
                        self._hooks.on_task_empty_result(uuid, task, stderr_text, metrics)

                    else:
                        metrics = TaskMetrics(
                            uuid=uuid, engine=engine, model=model,
                            wall_time_sec=round(finished_at - rt.started_at, 3),
                            token_count=token_count, status="success",
                            started_at=rt.started_at, finished_at=finished_at,
                            correlation_id=task.idempotency_key,
                        )
                        try:
                            fanout_spec = parse_fanout_spec(effective_result_path)
                        except ValueError as exc:
                            logger.warning("FanOut parse error for [%s]: %s", uuid, exc)
                            failure_metrics = TaskMetrics(
                                uuid=uuid, engine=engine, model=model,
                                wall_time_sec=round(finished_at - rt.started_at, 3),
                                token_count=token_count, status="failure",
                                started_at=rt.started_at, finished_at=finished_at,
                                correlation_id=task.idempotency_key,
                                failure_class=FailureClass.FANOUT_CHILD_FAILED,
                            )
                            state_mark_done(self._config.exec_done_dir, uuid, "FANOUT_CHILD_FAILED")
                            self._hooks.on_task_failure(uuid, task, 0, str(exc), failure_metrics)
                            continue
                        if fanout_spec:
                            self._spawn_fanout(uuid, task, fanout_spec, metrics)
                        else:
                            state_mark_done(self._config.exec_done_dir, uuid, 0)
                            self._hooks.on_task_success(uuid, task, metrics)
                            self._run_promote(effective_result_path)

                else:
                    state_mark_done(self._config.exec_done_dir, uuid, returncode)
                    metrics = TaskMetrics(
                        uuid=uuid, engine=engine, model=model,
                        wall_time_sec=round(finished_at - rt.started_at, 3),
                        token_count=token_count, status="failure",
                        started_at=rt.started_at, finished_at=finished_at,
                        correlation_id=task.idempotency_key,
                        failure_class=FailureClass.PROCESS_ERROR,
                    )
                    self._hooks.on_task_failure(uuid, task, returncode, stderr_text, metrics)

            except Exception as exc:
                logger.exception("Unexpected error handling completion for task [%s]: %s", uuid, exc)
                state_mark_done(self._config.exec_done_dir, uuid, "UNKNOWN_FAILURE")
                metrics = TaskMetrics(
                    uuid=uuid, engine=engine, model=model,
                    wall_time_sec=round(finished_at - rt.started_at, 3),
                    token_count=token_count, status="failure",
                    started_at=rt.started_at, finished_at=finished_at,
                    correlation_id=task.idempotency_key,
                    failure_class=FailureClass.UNKNOWN_FAILURE,
                )
                self._hooks.on_task_failure(uuid, task, -1, str(exc), metrics)

    def _spawn_fanout(self, parent_uuid: str, parent_task: Task,
                      spec: FanOutSpec, metrics: TaskMetrics) -> None:
        """Append child tasks to the exec file and register the parent in _fanout_pending."""
        exec_path = str(self._config.exec_md_path)
        child_uuids: set[str] = set()
        for child in spec.children:
            child_uuid = f"{parent_uuid}--fo--{child.id}"
            child_uuids.add(child_uuid)
            if exec_path.endswith(".jsonl"):
                line = build_child_jsonl_record(child_uuid, child.command)
            else:
                line = build_child_exec_line(child_uuid, child.command)
            self.append_task(line)
            logger.info("FanOut [%s]: spawned child [%s]", parent_uuid, child_uuid)
        self._fanout_pending[parent_uuid] = child_uuids
        self._fanout_tasks[parent_uuid] = parent_task
        self._fanout_metrics[parent_uuid] = metrics

    def _check_fanout_completions(self, known_done: set[str], known_succeeded: set[str]) -> None:
        """Check whether all children of each pending fan-out parent have completed."""
        for parent_uuid in list(self._fanout_pending):
            child_uuids = self._fanout_pending[parent_uuid]
            if not child_uuids.issubset(known_done):
                continue

            parent_task = self._fanout_tasks[parent_uuid]
            parent_metrics = self._fanout_metrics[parent_uuid]
            failed_children = child_uuids - known_succeeded

            if failed_children:
                state_mark_done(self._config.exec_done_dir, parent_uuid, "FANOUT_CHILD_FAILED")
                failure_metrics = TaskMetrics(
                    uuid=parent_uuid,
                    engine=parent_metrics.engine,
                    model=parent_metrics.model,
                    wall_time_sec=parent_metrics.wall_time_sec,
                    token_count=parent_metrics.token_count,
                    status="failure",
                    started_at=parent_metrics.started_at,
                    finished_at=parent_metrics.finished_at,
                    correlation_id=parent_metrics.correlation_id,
                    failure_class=FailureClass.FANOUT_CHILD_FAILED,
                )
                self._hooks.on_task_failure(
                    parent_uuid, parent_task, 0, "FANOUT_CHILD_FAILED", failure_metrics
                )
                known_done.add(parent_uuid)
            else:
                state_mark_done(self._config.exec_done_dir, parent_uuid, 0)
                self._hooks.on_task_success(parent_uuid, parent_task, parent_metrics)
                known_done.add(parent_uuid)
                known_succeeded.add(parent_uuid)
                parent_result_path = parent_task.result_path or _extract_tee_target(parent_task.command)
                self._run_promote(parent_result_path)

            del self._fanout_pending[parent_uuid]
            del self._fanout_tasks[parent_uuid]
            del self._fanout_metrics[parent_uuid]
            logger.info("FanOut join complete for [%s] (failed_children=%s)", parent_uuid, failed_children)

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
                repo_root=Path(self._config.exec_md_path).parent,
            )
        except Exception:
            logger.warning("Promote failed for %s → %s", result_path, promote_target, exc_info=True)

    def _join_reader_threads(self, rt: "RunningTask") -> None:
        for name, th in [("stderr", rt.stderr_thread), ("stdout", rt.stdout_thread)]:
            if th is None:
                continue
            th.join(timeout=2.0)
            if th.is_alive():
                logger.warning(
                    "Task [%s] %s reader thread did not terminate within 2.0s",
                    rt.uuid, name,
                )

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
