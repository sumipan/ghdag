"""DagHooks Protocol and DefaultHooks implementation."""

from __future__ import annotations

import logging
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
    def check_promote_target(self, result_path: str) -> "str | None": ...


class DefaultHooks:
    """Default implementation of DagHooks — logging only."""

    def on_task_start(self, uuid: str, task: Task) -> None:
        logger.info("Task started: %s", uuid)

    def on_task_success(self, uuid: str, task: Task, metrics: TaskMetrics) -> None:
        logger.info("Task succeeded: %s", uuid)

    def on_task_failure(self, uuid: str, task: Task, returncode: int, stderr_text: str, metrics: TaskMetrics) -> None:
        logger.warning("Task failed: %s (returncode=%d)", uuid, returncode)

    def on_task_rejected(self, uuid: str, task: Task, retry_depth: int, is_final: bool, metrics: TaskMetrics) -> None:
        logger.warning("Task rejected: %s (retry_depth=%d, is_final=%s)", uuid, retry_depth, is_final)

    def on_task_dep_failed(self, uuid: str, task: Task, failed_dep: str) -> None:
        logger.info("Task dep-failed: %s (failed_dep=%s)", uuid, failed_dep)

    def on_task_empty_result(self, uuid: str, task: Task, stderr_text: str, metrics: TaskMetrics) -> None:
        logger.warning("Task empty result: %s", uuid)

    def on_shutdown(self, signum: int) -> None:
        logger.info("Shutdown signal received: %d", signum)

    def check_rejected(self, result_path: str) -> bool:
        from ._util import default_check_rejected
        return default_check_rejected(result_path)

    def check_pipeline_status(self, result_path: str) -> "str | None":
        from ._util import check_pipeline_status
        return check_pipeline_status(result_path)

    def check_promote_target(self, result_path: str) -> "str | None":
        """Return the target path to promote result_path to, or None to skip.

        Opt-in: only returns a non-None value when a hook subclass overrides this.
        Callers must never auto-promote without an explicit non-None return value.
        """
        return None
