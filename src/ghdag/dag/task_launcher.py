"""TaskLauncher — subprocess launch, I/O threads, and completion detection."""

from __future__ import annotations

import io
import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ghdag.llm.adapters import get_output_adapter
from ghdag.metrics.models import FailureClass, TaskMetrics
from ghdag.metrics.parsers import parse_engine_model

from ._util import _extract_tee_target, _stderr_reader, _stdout_reader
from .circuit_breaker import CircuitBreakerPolicy
from .fanout import parse_fanout_spec
from .fanout_manager import FanOutManager
from .hooks import DagHooks
from .models import DagConfig, RunningTask, Task
from .state import mark_done as state_mark_done

logger = logging.getLogger(__name__)

_STDIN_REDIR_RE = re.compile(r"(?<!<)<\s+(\S+)")


def _task_request_id(task: Task) -> str | None:
    return task.annotations.get("_request_id")


class TaskLauncher:
    """Manage subprocess launch and completion detection for DAG tasks."""

    def __init__(
        self,
        config: DagConfig,
        hooks: DagHooks,
        circuit_breaker: CircuitBreakerPolicy,
        fanout_manager: FanOutManager,
        promote_fn: Callable[[str | None], None],
    ) -> None:
        self._config = config
        self._hooks = hooks
        self._circuit_breaker = circuit_breaker
        self._fanout_manager = fanout_manager
        self._promote_fn = promote_fn
        self._running: dict[str, RunningTask] = {}

    def launch(self, uuid: str, task: Task) -> None:
        """Start a subprocess for the given task and register it in _running."""
        logger.info("Launching [%s]: %s", uuid, task.command)
        cwd = str(self._config.cwd) if self._config.cwd else None

        m = _STDIN_REDIR_RE.search(task.command)
        if m:
            input_file = m.group(1)
            base = Path(cwd) if cwd else Path()
            check_path = (
                Path(input_file)
                if Path(input_file).is_absolute()
                else base / input_file
            )
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

    def check_completions(self) -> None:
        """Inspect running processes and process any that have finished."""
        for uuid in list(self._running):
            rt = self._running[uuid]

            task_timeout = self._config.task_timeout
            if task_timeout is not None and rt.proc.poll() is None:
                now = time.monotonic()
                if rt.term_sent_at is None and (now - rt.started_at_mono) > task_timeout:
                    logger.warning(
                        "Task [%s] exceeded timeout %.1fs, sending SIGTERM", uuid, task_timeout
                    )
                    rt.proc.terminate()
                    rt.term_sent_at = now
                elif rt.term_sent_at is not None and (now - rt.term_sent_at) > self._config.kill_grace:
                    logger.warning(
                        "Task [%s] still alive after grace period, sending SIGKILL", uuid
                    )
                    rt.proc.kill()

            if rt.proc.poll() is None:
                continue

            was_timeout = rt.term_sent_at is not None
            finished_at = time.time()
            del self._running[uuid]
            self._join_reader_threads(rt)
            stderr_bytes = rt.stderr_buf.getvalue()
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            returncode = rt.proc.returncode

            task = rt.task
            engine = task.engine
            model = task.model
            if engine is None:
                engine, model = parse_engine_model(task.command)

            stdout_data = rt.stdout_buf.getvalue() if rt.stdout_buf else b""
            adapter = get_output_adapter(engine)
            usage = adapter.extract_token_usage(stdout_data, stderr_bytes)
            token_count = usage.token_count if usage else None
            cost_usd = usage.cost_usd if usage else None
            cache_read_tokens = usage.cache_read_tokens if usage else None
            cache_creation_tokens = usage.cache_creation_tokens if usage else None

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
                        request_id=_task_request_id(task),
                        cost_usd=cost_usd,
                        cache_read_tokens=cache_read_tokens,
                        cache_creation_tokens=cache_creation_tokens,
                    )
                    timeout_msg = (
                        f"TIMEOUT: task exceeded task_timeout={self._config.task_timeout}s"
                    )
                    self._hooks.on_task_failure(uuid, task, returncode, timeout_msg, metrics)
                    self._circuit_breaker.record_failure()
                    continue

                if returncode == 0:
                    if task.result_path is not None:
                        transformed = adapter.extract_result_text(stdout_data, stderr_bytes)
                        rp = Path(task.result_path)
                        policy = task.result_finalize or "preserve_nonempty"
                        if policy == "stdout_only" or not (rp.exists() and rp.stat().st_size > 0):
                            rp.write_bytes(transformed)
                        effective_result_path: str | None = task.result_path
                    else:
                        effective_result_path = _extract_tee_target(task.command)

                    if effective_result_path and self._hooks.check_rejected(effective_result_path):
                        retry_depth = task.retry
                        is_final = retry_depth >= self._config.max_retry
                        if is_final:
                            state_mark_done(self._config.exec_done_dir, uuid, "REJECTED_FINAL")
                        else:
                            state_mark_done(self._config.exec_done_dir, uuid, "REJECTED")
                            if task.result_path and Path(task.result_path).exists():
                                Path(task.result_path).unlink()
                        metrics = TaskMetrics(
                            uuid=uuid, engine=engine, model=model,
                            wall_time_sec=round(finished_at - rt.started_at, 3),
                            token_count=token_count, status="rejected",
                            started_at=rt.started_at, finished_at=finished_at,
                            correlation_id=task.idempotency_key,
                            failure_class=FailureClass.REJECTED,
                            request_id=_task_request_id(task),
                            cost_usd=cost_usd,
                            cache_read_tokens=cache_read_tokens,
                            cache_creation_tokens=cache_creation_tokens,
                        )
                        self._hooks.on_task_rejected(uuid, task, retry_depth, is_final, metrics)

                    elif effective_result_path and (
                        pipeline_status := self._hooks.check_pipeline_status(effective_result_path)
                    ) and pipeline_status.endswith("_FAILED"):
                        state_mark_done(
                            self._config.exec_done_dir, uuid,
                            f"PIPELINE_FAILED:{pipeline_status}",
                        )
                        metrics = TaskMetrics(
                            uuid=uuid, engine=engine, model=model,
                            wall_time_sec=round(finished_at - rt.started_at, 3),
                            token_count=token_count, status="failure",
                            started_at=rt.started_at, finished_at=finished_at,
                            correlation_id=task.idempotency_key,
                            failure_class=FailureClass.PIPELINE_FAILED,
                            request_id=_task_request_id(task),
                            cost_usd=cost_usd,
                            cache_read_tokens=cache_read_tokens,
                            cache_creation_tokens=cache_creation_tokens,
                        )
                        self._hooks.on_task_failure(
                            uuid, task, 0, f"PIPELINE_FAILED:{pipeline_status}", metrics
                        )
                        self._circuit_breaker.record_failure()

                    elif (
                        effective_result_path
                        and os.path.exists(effective_result_path)
                        and os.path.getsize(effective_result_path) == 0
                    ):
                        state_mark_done(self._config.exec_done_dir, uuid, "EMPTY_RESULT")
                        metrics = TaskMetrics(
                            uuid=uuid, engine=engine, model=model,
                            wall_time_sec=round(finished_at - rt.started_at, 3),
                            token_count=token_count, status="empty_result",
                            started_at=rt.started_at, finished_at=finished_at,
                            correlation_id=task.idempotency_key,
                            failure_class=FailureClass.EMPTY_RESULT,
                            request_id=_task_request_id(task),
                            cost_usd=cost_usd,
                            cache_read_tokens=cache_read_tokens,
                            cache_creation_tokens=cache_creation_tokens,
                        )
                        self._hooks.on_task_empty_result(uuid, task, stderr_text, metrics)

                    else:
                        metrics = TaskMetrics(
                            uuid=uuid, engine=engine, model=model,
                            wall_time_sec=round(finished_at - rt.started_at, 3),
                            token_count=token_count, status="success",
                            started_at=rt.started_at, finished_at=finished_at,
                            correlation_id=task.idempotency_key,
                            request_id=_task_request_id(task),
                            cost_usd=cost_usd,
                            cache_read_tokens=cache_read_tokens,
                            cache_creation_tokens=cache_creation_tokens,
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
                                failure_class=FailureClass.FANOUT_PARSE_FAILED,
                                request_id=_task_request_id(task),
                                cost_usd=cost_usd,
                                cache_read_tokens=cache_read_tokens,
                                cache_creation_tokens=cache_creation_tokens,
                            )
                            state_mark_done(
                                self._config.exec_done_dir, uuid, "FANOUT_PARSE_FAILED"
                            )
                            self._hooks.on_task_failure(uuid, task, 0, str(exc), failure_metrics)
                            continue
                        if fanout_spec:
                            self._fanout_manager.spawn(uuid, task, fanout_spec, metrics)
                        else:
                            state_mark_done(self._config.exec_done_dir, uuid, 0)
                            self._hooks.on_task_success(uuid, task, metrics)
                            self._circuit_breaker.reset()
                            self._promote_fn(effective_result_path)

                else:
                    state_mark_done(self._config.exec_done_dir, uuid, returncode)
                    metrics = TaskMetrics(
                        uuid=uuid, engine=engine, model=model,
                        wall_time_sec=round(finished_at - rt.started_at, 3),
                        token_count=token_count, status="failure",
                        started_at=rt.started_at, finished_at=finished_at,
                        correlation_id=task.idempotency_key,
                        failure_class=FailureClass.PROCESS_ERROR,
                        request_id=_task_request_id(task),
                        cost_usd=cost_usd,
                        cache_read_tokens=cache_read_tokens,
                        cache_creation_tokens=cache_creation_tokens,
                    )
                    self._hooks.on_task_failure(uuid, task, returncode, stderr_text, metrics)
                    self._circuit_breaker.record_failure()

            except Exception as exc:
                logger.exception(
                    "Unexpected error handling completion for task [%s]: %s", uuid, exc
                )
                state_mark_done(self._config.exec_done_dir, uuid, "UNKNOWN_FAILURE")
                metrics = TaskMetrics(
                    uuid=uuid, engine=engine, model=model,
                    wall_time_sec=round(finished_at - rt.started_at, 3),
                    token_count=token_count, status="failure",
                    started_at=rt.started_at, finished_at=finished_at,
                    correlation_id=task.idempotency_key,
                    failure_class=FailureClass.UNKNOWN_FAILURE,
                    request_id=_task_request_id(task),
                    cost_usd=cost_usd,
                    cache_read_tokens=cache_read_tokens,
                    cache_creation_tokens=cache_creation_tokens,
                )
                self._hooks.on_task_failure(uuid, task, -1, str(exc), metrics)

    def is_running(self, uuid: str) -> bool:
        """Return True if the given task uuid is currently running."""
        return uuid in self._running

    @property
    def running_count(self) -> int:
        """Number of currently running tasks."""
        return len(self._running)

    def _join_reader_threads(self, rt: RunningTask) -> None:
        for name, th in [("stderr", rt.stderr_thread), ("stdout", rt.stdout_thread)]:
            if th is None:
                continue
            th.join(timeout=2.0)
            if th.is_alive():
                logger.warning(
                    "Task [%s] %s reader thread did not terminate within 2.0s",
                    rt.uuid, name,
                )
