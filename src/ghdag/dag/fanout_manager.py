"""FanOutManager — child task generation and join logic."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ghdag.core.capabilities import PRESETS
from ghdag.core.command import get_adapter
from ghdag.core.models.metrics import FailureClass, TaskMetrics
from ghdag.core.vocabulary import DONE_FANOUT_CHILD_FAILED, FANOUT_SEPARATOR
from ghdag.io.done import mark_done as state_mark_done
from ghdag.io.exec_jsonl import DuplicateTaskError
from ghdag.llm.engines import EngineModelError, validate_engine_model

from .fanout import FanoutError, FanOutSpec, build_child_jsonl_record, build_child_record_json
from .hooks import DagHooks
from .models import DagConfig, Task

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Asia/Tokyo")


def _sibling_uuid(parent_uuid: str, sibling_id: str) -> str:
    return f"{parent_uuid}{FANOUT_SEPARATOR}{sibling_id}"


def _resolve_depends_uuids(parent_uuid: str, depends: tuple[str, ...]) -> list[str]:
    return [_sibling_uuid(parent_uuid, dep) for dep in depends]


class FanOutManager:
    """Manage child task spawning and parent join for fan-out/join semantics."""

    def __init__(
        self,
        config: DagConfig,
        hooks: DagHooks,
        append_task_fn: Callable[[str, str], None],
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
        *,
        queue_dir: Path | None = None,
    ) -> None:
        """Append child tasks to exec file and register parent as pending."""
        queue = queue_dir or Path(self._config.exec_jsonl_path).parent
        parent_engine = parent_task.engine or metrics.engine
        parent_model = parent_task.model or metrics.model

        staged_children: list[tuple[str, str]] = []
        for child in spec.children:
            child_uuid = _sibling_uuid(parent_uuid, child.id)
            if child.prompt is not None:
                line = self._spawn_prompt_child(
                    parent_uuid=parent_uuid,
                    parent_engine=parent_engine,
                    parent_model=parent_model,
                    child=child,
                    child_uuid=child_uuid,
                    queue_dir=queue,
                )
            else:
                line = build_child_jsonl_record(child_uuid, child.command or "")
            staged_children.append((child_uuid, line))

        # Validate/build every child before appending any record.  In particular,
        # a bad model on a later prompt child must not leave earlier siblings queued
        # when the parent enters a contract-repair turn.
        child_uuids = {child_uuid for child_uuid, _ in staged_children}
        for child_uuid, line in staged_children:
            try:
                self._append_task_fn(line, parent_uuid)
            except DuplicateTaskError:
                logger.info(
                    "FanOut [%s]: child [%s] already queued (not done); treating as spawned",
                    parent_uuid,
                    child_uuid,
                )
            else:
                logger.info("FanOut [%s]: spawned child [%s]", parent_uuid, child_uuid)
        self._pending[parent_uuid] = child_uuids
        self._tasks[parent_uuid] = parent_task
        self._metrics[parent_uuid] = metrics

    def _spawn_prompt_child(
        self,
        *,
        parent_uuid: str,
        parent_engine: str | None,
        parent_model: str | None,
        child,
        child_uuid: str,
        queue_dir: Path,
    ) -> str:
        effective_engine = child.engine or parent_engine
        if not effective_engine or effective_engine == "shell":
            raise FanoutError(
                f"child {child.id!r}: prompt children require an LLM engine",
                hint="set engine on the child or ensure the parent task has an LLM engine",
            )

        if child.engine and child.model is not None:
            effective_model = child.model
        elif child.engine and child.engine != parent_engine:
            effective_model = None
        elif child.model is not None:
            effective_model = child.model
        else:
            effective_model = parent_model if effective_engine == parent_engine else None

        try:
            validate_engine_model(effective_engine, effective_model)
        except EngineModelError as exc:
            raise FanoutError(
                f"child {child.id!r}: {exc.message}",
                hint=exc.hint,
            ) from exc

        record_model = (
            child.model
            if child.model is not None
            else (parent_model if effective_engine == parent_engine else None)
        )

        ts = datetime.now(_TZ).strftime("%Y%m%d%H%M%S")
        order_name = f"{ts}-{effective_engine}-order-{child_uuid}.md"
        order_path = queue_dir / order_name
        order_path.parent.mkdir(parents=True, exist_ok=True)
        order_path.write_text(child.prompt or "", encoding="utf-8")

        result_name = f"{ts}-{effective_engine}-result-{child_uuid}.md"
        result_path = str(queue_dir / result_name)
        depends = _resolve_depends_uuids(parent_uuid, child.depends)
        preset = child.permission or "text_only"
        capabilities = PRESETS[preset]

        record = get_adapter(effective_engine).build_exec_record(
            uuid=child_uuid,
            order_path=str(order_path),
            result_path=result_path,
            model=record_model,
            depends=depends,
            prompt=None,
            capabilities=capabilities,
        )
        annotations = dict(record.get("annotations") or {})
        annotations["fanout_parent"] = parent_uuid
        annotations["step_name"] = child.id
        record["annotations"] = annotations
        return build_child_record_json(record)

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
                state_mark_done(self._config.exec_done_dir, parent_uuid, DONE_FANOUT_CHILD_FAILED)
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
                    parent_uuid, parent_task, 0, DONE_FANOUT_CHILD_FAILED, failure_metrics
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
