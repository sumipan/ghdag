"""FanOutManager — child task generation and join logic."""

from __future__ import annotations

import logging
from collections.abc import Callable

from ghdag.metrics.models import FailureClass, TaskMetrics

from .fanout import FanOutSpec, build_child_jsonl_record
from .hooks import DagHooks
from .models import DagConfig, Task
from .state import mark_done as state_mark_done

logger = logging.getLogger(__name__)


class FanOutManager:
    """Manage child task spawning and parent join for fan-out/join semantics."""

    def __init__(
        self,
        config: DagConfig,
        hooks: DagHooks,
        append_task_fn: Callable[[str], None],
        promote_fn: Callable[[str | None], None],
    ) -> None:
        self._config = config
        self._hooks = hooks
        self._append_task_fn = append_task_fn
        self._promote_fn = promote_fn
        self._pending: dict[str, set[str]] = {}
        self._tasks: dict[str, Task] = {}
        self._metrics: dict[str, TaskMetrics] = {}

    def spawn(
        self,
        parent_uuid: str,
        parent_task: Task,
        spec: FanOutSpec,
        metrics: TaskMetrics,
    ) -> None:
        """Append child tasks to exec file and register parent as pending."""
        child_uuids: set[str] = set()
        for child in spec.children:
            child_uuid = f"{parent_uuid}--fo--{child.id}"
            child_uuids.add(child_uuid)
            line = build_child_jsonl_record(child_uuid, child.command)
            self._append_task_fn(line)
            logger.info("FanOut [%s]: spawned child [%s]", parent_uuid, child_uuid)
        self._pending[parent_uuid] = child_uuids
        self._tasks[parent_uuid] = parent_task
        self._metrics[parent_uuid] = metrics

    def check_completions(self, known_done: set[str], known_succeeded: set[str]) -> None:
        """Mark parent done when all its children have completed."""
        for parent_uuid in list(self._pending):
            child_uuids = self._pending[parent_uuid]
            if not child_uuids.issubset(known_done):
                continue

            parent_task = self._tasks[parent_uuid]
            parent_metrics = self._metrics[parent_uuid]
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
                    request_id=parent_metrics.request_id,
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
                from ._util import _extract_tee_target
                parent_result_path = parent_task.result_path or _extract_tee_target(parent_task.command)
                self._promote_fn(parent_result_path)

            del self._pending[parent_uuid]
            del self._tasks[parent_uuid]
            del self._metrics[parent_uuid]
            logger.info(
                "FanOut join complete for [%s] (failed_children=%s)",
                parent_uuid, failed_children,
            )

    def is_pending(self, uuid: str) -> bool:
        """Return True if the given uuid is awaiting fan-out child completion."""
        return uuid in self._pending
