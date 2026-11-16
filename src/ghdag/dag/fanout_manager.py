"""FanOutManager — child task generation and join logic."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from ghdag.core.capabilities import PERMISSIONS
from ghdag.core.command import get_command_adapter
from ghdag.core.models.metrics import FailureClass, TaskMetrics
from ghdag.core.vocabulary import DONE_FANOUT_CHILD_FAILED, FANOUT_SEPARATOR
from ghdag.io.audit import file_ts
from ghdag.io.done import mark_done as state_mark_done
from ghdag.io.exec_jsonl import DuplicateTaskError
from ghdag.llm.engines import EngineModelError, validate_engine_model

from .fanout import FanoutError, FanOutSpec, build_child_jsonl_record, build_child_record_json
from .hooks import DagHooks
from .models import DagConfig, Task

logger = logging.getLogger(__name__)


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
        self._write_fanout_pending(parent_uuid)

    def _state_dir(self) -> Path:
        return Path(self._config.exec_done_dir).parent

    def _fanout_pending_path(self, parent_uuid: str) -> Path:
        return self._state_dir() / "fanout-pending" / f"{parent_uuid}.json"

    @staticmethod
    def _metrics_to_dict(metrics: TaskMetrics) -> dict:
        return {
            "uuid": metrics.uuid,
            "engine": metrics.engine,
            "model": metrics.model,
            "wall_time_sec": metrics.wall_time_sec,
            "token_count": metrics.token_count,
            "status": metrics.status,
            "started_at": metrics.started_at,
            "finished_at": metrics.finished_at,
            "correlation_id": metrics.correlation_id,
            "failure_class": (
                metrics.failure_class.name if metrics.failure_class is not None else None
            ),
            "failure_class_reason": metrics.failure_class_reason,
            "request_id": metrics.request_id,
            "cost_usd": metrics.cost_usd,
            "cache_read_tokens": metrics.cache_read_tokens,
            "cache_creation_tokens": metrics.cache_creation_tokens,
            "additional_tags": metrics.additional_tags,
            "stderr_path": metrics.stderr_path,
        }

    @staticmethod
    def _metrics_from_dict(data: dict) -> TaskMetrics | None:
        if not isinstance(data, dict):
            return None
        required = (
            "uuid",
            "wall_time_sec",
            "status",
            "started_at",
            "finished_at",
        )
        for key in required:
            if key not in data:
                return None
        fc_raw = data.get("failure_class")
        failure_class = None
        if fc_raw is not None:
            if not isinstance(fc_raw, str):
                return None
            try:
                failure_class = FailureClass[fc_raw]
            except KeyError:
                return None
        try:
            return TaskMetrics(
                uuid=str(data["uuid"]),
                engine=data.get("engine"),
                model=data.get("model"),
                wall_time_sec=float(data["wall_time_sec"]),
                token_count=data.get("token_count"),
                status=str(data["status"]),
                started_at=float(data["started_at"]),
                finished_at=float(data["finished_at"]),
                correlation_id=data.get("correlation_id"),
                failure_class=failure_class,
                failure_class_reason=data.get("failure_class_reason"),
                request_id=data.get("request_id"),
                cost_usd=data.get("cost_usd"),
                cache_read_tokens=data.get("cache_read_tokens"),
                cache_creation_tokens=data.get("cache_creation_tokens"),
                additional_tags=data.get("additional_tags"),
                stderr_path=data.get("stderr_path"),
            )
        except (TypeError, ValueError):
            return None

    def _write_fanout_pending(self, parent_uuid: str) -> None:
        child_uuids = self._pending.get(parent_uuid)
        metrics = self._metrics.get(parent_uuid)
        if child_uuids is None or metrics is None:
            return
        path = self._fanout_pending_path(parent_uuid)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "children": sorted(child_uuids),
            "metrics": self._metrics_to_dict(metrics),
        }
        fd, tmp = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _remove_fanout_pending(self, parent_uuid: str) -> None:
        path = self._fanout_pending_path(parent_uuid)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "Failed to remove fanout-pending file for [%s]", parent_uuid, exc_info=True
            )

    def restore(self, tasks: dict[str, Task], known_done: set[str]) -> list[str]:
        """Reload fan-out parent wait state from disk after restart."""
        pending_dir = self._state_dir() / "fanout-pending"
        if not pending_dir.is_dir():
            return []
        restored: list[str] = []
        for path in sorted(pending_dir.glob("*.json")):
            parent_uuid = path.stem
            if parent_uuid not in tasks or parent_uuid in known_done:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "Failed to remove stale fanout-pending file %s",
                        path,
                        exc_info=True,
                    )
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning("Corrupt fanout-pending file %s — ignoring", path)
                continue
            if not isinstance(payload, dict):
                logger.warning("Corrupt fanout-pending file %s — ignoring", path)
                continue
            children = payload.get("children")
            metrics_data = payload.get("metrics")
            if not isinstance(children, list) or not all(
                isinstance(c, str) for c in children
            ):
                logger.warning("Corrupt fanout-pending file %s — ignoring", path)
                continue
            metrics = self._metrics_from_dict(metrics_data)
            if metrics is None:
                logger.warning("Corrupt fanout-pending file %s — ignoring", path)
                continue
            self._pending[parent_uuid] = set(children)
            self._tasks[parent_uuid] = tasks[parent_uuid]
            self._metrics[parent_uuid] = metrics
            restored.append(parent_uuid)
        return restored

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

        ts = file_ts(self._config.timezone)
        order_name = f"{ts}-{effective_engine}-order-{child_uuid}.md"
        order_path = queue_dir / order_name
        order_path.parent.mkdir(parents=True, exist_ok=True)
        order_path.write_text(child.prompt or "", encoding="utf-8")

        result_name = f"{ts}-{effective_engine}-result-{child_uuid}.md"
        result_path = str(queue_dir / result_name)
        depends = _resolve_depends_uuids(parent_uuid, child.depends)
        preset = child.permission or "text_only"
        perm = PERMISSIONS[preset]

        record = get_command_adapter(effective_engine).build_exec_record(
            uuid=child_uuid,
            order_path=str(order_path),
            result_path=result_path,
            model=record_model,
            depends=depends,
            prompt=None,
            permission=perm,
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

            self._remove_fanout_pending(parent_uuid)
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
