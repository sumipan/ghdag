"""AuditHooks — DefaultHooks + audit.jsonl 書き込み。"""

from __future__ import annotations

from pathlib import Path

from ghdag.dag.hooks import DefaultHooks
from ghdag.dag.models import Task
from ghdag.metrics.models import FailureClass, TaskMetrics
from ghdag.pipeline.audit import write_task_exit_audit


class AuditHooks(DefaultHooks):
    """DefaultHooks に audit.jsonl 書き込みを追加した実装。"""

    def __init__(self, audit_path: Path | None = None) -> None:
        self._audit_path = audit_path

    def on_task_start(self, uuid: str, task: Task) -> None:
        super().on_task_start(uuid, task)
        if self._audit_path:
            write_task_exit_audit(
                self._audit_path,
                event_type="task_started", uuid=uuid, status="running",
            )

    def on_task_success(self, uuid: str, task: Task, metrics: TaskMetrics) -> None:
        super().on_task_success(uuid, task, metrics)
        if self._audit_path:
            write_task_exit_audit(
                self._audit_path,
                event_type="task_complete", uuid=uuid, status="success",
                elapsed_sec=metrics.wall_time_sec, token_count=metrics.token_count,
                model=metrics.model, engine=metrics.engine,
                correlation_id=metrics.correlation_id,
                failure_class=metrics.failure_class,
            )

    def on_task_failure(self, uuid: str, task: Task, returncode: int, stderr_text: str, metrics: TaskMetrics) -> None:
        super().on_task_failure(uuid, task, returncode, stderr_text, metrics)
        if self._audit_path:
            write_task_exit_audit(
                self._audit_path,
                event_type="task_failed", uuid=uuid, status="failure",
                elapsed_sec=metrics.wall_time_sec, token_count=metrics.token_count,
                model=metrics.model, engine=metrics.engine,
                correlation_id=metrics.correlation_id,
                failure_class=metrics.failure_class,
            )

    def on_task_rejected(self, uuid: str, task: Task, retry_depth: int, is_final: bool, metrics: TaskMetrics) -> None:
        super().on_task_rejected(uuid, task, retry_depth, is_final, metrics)
        if self._audit_path:
            write_task_exit_audit(
                self._audit_path,
                event_type="task_rejected", uuid=uuid, status="rejected",
                elapsed_sec=metrics.wall_time_sec, token_count=metrics.token_count,
                model=metrics.model, engine=metrics.engine,
                correlation_id=metrics.correlation_id,
                failure_class=metrics.failure_class,
            )

    def on_task_dep_failed(self, uuid: str, task: Task, failed_dep: str) -> None:
        super().on_task_dep_failed(uuid, task, failed_dep)
        if self._audit_path:
            write_task_exit_audit(
                self._audit_path,
                event_type="task_dep_failed", uuid=uuid, status="dep_failed",
                correlation_id=task.idempotency_key,
                failure_class=FailureClass.DEP_FAILED,
            )

    def on_task_empty_result(self, uuid: str, task: Task, stderr_text: str, metrics: TaskMetrics) -> None:
        super().on_task_empty_result(uuid, task, stderr_text, metrics)
        if self._audit_path:
            write_task_exit_audit(
                self._audit_path,
                event_type="task_empty_result", uuid=uuid, status="empty_result",
                elapsed_sec=metrics.wall_time_sec, token_count=metrics.token_count,
                model=metrics.model, engine=metrics.engine,
                correlation_id=metrics.correlation_id,
                failure_class=metrics.failure_class,
            )
