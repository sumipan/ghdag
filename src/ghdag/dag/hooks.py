"""DagHooks Protocol and DefaultHooks implementation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from .models import Task
from ghdag.metrics.models import TaskMetrics

logger = logging.getLogger(__name__)


class DagHooks(Protocol):
    def on_task_start(self, uuid: str, task: Task) -> None: ...
    def on_task_success(self, uuid: str, task: Task, metrics: TaskMetrics) -> None: ...
    def on_task_failure(self, uuid: str, task: Task, returncode: int, stderr_text: str, metrics: TaskMetrics) -> None: ...
    def on_task_rejected(self, uuid: str, task: Task, retry_depth: int, is_final: bool, metrics: TaskMetrics) -> None: ...
    def on_task_dep_failed(self, uuid: str, task: Task, failed_dep: str) -> None: ...
    def on_task_empty_result(self, uuid: str, task: Task, stderr_text: str, metrics: TaskMetrics) -> None: ...
    def on_shutdown(self, signum: int) -> None: ...
    def check_rejected(self, result_path: str) -> bool: ...
    def check_pipeline_status(self, result_path: str) -> "str | None": ...


class DefaultHooks:
    """Default implementation of DagHooks — logging only."""

    def __init__(self, audit_path: Path | None = None) -> None:
        self._audit_path = audit_path

    def on_task_start(self, uuid: str, task: Task) -> None:
        logger.info("Task started: %s", uuid)
        if self._audit_path:
            from ghdag.pipeline.audit import write_task_exit_audit
            write_task_exit_audit(
                self._audit_path,
                event_type="task_started", uuid=uuid, status="running",
            )

    def on_task_success(self, uuid: str, task: Task, metrics: TaskMetrics) -> None:
        logger.info("Task succeeded: %s", uuid)
        if self._audit_path:
            from ghdag.pipeline.audit import write_task_exit_audit
            write_task_exit_audit(
                self._audit_path,
                event_type="task_complete", uuid=uuid, status="success",
                elapsed_sec=metrics.wall_time_sec, token_count=metrics.token_count,
                model=metrics.model, engine=metrics.engine,
                correlation_id=metrics.correlation_id,
                failure_class=metrics.failure_class,
            )

    def on_task_failure(self, uuid: str, task: Task, returncode: int, stderr_text: str, metrics: TaskMetrics) -> None:
        logger.warning("Task failed: %s (returncode=%d)", uuid, returncode)
        if self._audit_path:
            from ghdag.pipeline.audit import write_task_exit_audit
            write_task_exit_audit(
                self._audit_path,
                event_type="task_failed", uuid=uuid, status="failure",
                elapsed_sec=metrics.wall_time_sec, token_count=metrics.token_count,
                model=metrics.model, engine=metrics.engine,
                correlation_id=metrics.correlation_id,
                failure_class=metrics.failure_class,
            )

    def on_task_rejected(self, uuid: str, task: Task, retry_depth: int, is_final: bool, metrics: TaskMetrics) -> None:
        logger.warning("Task rejected: %s (retry_depth=%d, is_final=%s)", uuid, retry_depth, is_final)
        if self._audit_path:
            from ghdag.pipeline.audit import write_task_exit_audit
            write_task_exit_audit(
                self._audit_path,
                event_type="task_rejected", uuid=uuid, status="rejected",
                elapsed_sec=metrics.wall_time_sec, token_count=metrics.token_count,
                model=metrics.model, engine=metrics.engine,
                correlation_id=metrics.correlation_id,
                failure_class=metrics.failure_class,
            )

    def on_task_dep_failed(self, uuid: str, task: Task, failed_dep: str) -> None:
        logger.info("Task dep-failed: %s (failed_dep=%s)", uuid, failed_dep)
        if self._audit_path:
            from ghdag.pipeline.audit import write_task_exit_audit
            write_task_exit_audit(
                self._audit_path,
                event_type="task_dep_failed", uuid=uuid, status="dep_failed",
                correlation_id=task.idempotency_key,
                failure_class="DEP_FAILED",
            )

    def on_task_empty_result(self, uuid: str, task: Task, stderr_text: str, metrics: TaskMetrics) -> None:
        logger.warning("Task empty result: %s", uuid)
        if self._audit_path:
            from ghdag.pipeline.audit import write_task_exit_audit
            write_task_exit_audit(
                self._audit_path,
                event_type="task_empty_result", uuid=uuid, status="empty_result",
                elapsed_sec=metrics.wall_time_sec, token_count=metrics.token_count,
                model=metrics.model, engine=metrics.engine,
                correlation_id=metrics.correlation_id,
                failure_class=metrics.failure_class,
            )

    def on_shutdown(self, signum: int) -> None:
        logger.info("Shutdown signal received: %d", signum)

    def check_rejected(self, result_path: str) -> bool:
        from ._util import default_check_rejected
        return default_check_rejected(result_path)

    def check_pipeline_status(self, result_path: str) -> "str | None":
        from ._util import check_pipeline_status
        return check_pipeline_status(result_path)
