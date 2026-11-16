"""pipeline/task_queue.py — TaskQueue: order files, exec.jsonl, and idempotency in one place."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from ghdag.config.env import engine_isolation_enabled, ghdag_safe_default_permission
from ghdag.config.env import state_dir as resolve_state_dir
from ghdag.core.exceptions import ConfigError
from ghdag.io import exec_jsonl
from ghdag.io.audit import AuditContext, file_ts
from ghdag.pipeline.llm_pipeline import validate_depends
from ghdag.pipeline.order import OrderBuilder
from ghdag.quota import QuotaGate

if TYPE_CHECKING:
    from ghdag.core.models.workflow import StepConfig


class TaskQueue:
    """Handles order/result files, exec.jsonl enqueue, and pipeline bookkeeping."""

    def __init__(
        self,
        state_dir: str | Path,
        exec_jsonl_path: str | Path,
        *,
        queue_dir: str = "queue",
        order_builder: OrderBuilder | None = None,
        order_builders: dict[str, OrderBuilder] | None = None,
        timezone: str | None = None,
    ) -> None:
        self._state_dir = Path(state_dir)
        self._exec_jsonl_path = Path(exec_jsonl_path)
        self._queue_dir = queue_dir
        self._order_builder = order_builder
        self._order_builders: dict[str, OrderBuilder] = dict(order_builders or {})
        self._timezone = timezone
        self._quota_gate = QuotaGate(
            resolve_state_dir(self._exec_jsonl_path.parent) / "quota-gate.json",
            audit_path=self._exec_jsonl_path.parent / "audit.jsonl",
        )

    @property
    def state_dir(self) -> Path:
        return self._state_dir

    @property
    def exec_jsonl_path(self) -> Path:
        return self._exec_jsonl_path

    @property
    def _generations_path(self) -> Path:
        return self._state_dir / "generations.json"

    @classmethod
    def from_repo_root(cls, repo_root: str | Path, **kwargs: object) -> TaskQueue:
        """Create TaskQueue with standard paths under the repository root."""
        root = Path(repo_root)
        return cls(
            state_dir=resolve_state_dir(root) / ".pipeline-state",
            exec_jsonl_path=root / "jobs" / "exec.jsonl",
            **kwargs,
        )

    def set_order_builders(self, mapping: Mapping[str, OrderBuilder]) -> None:
        """Replace the per-workflow order builder map (default builder unchanged)."""
        self._order_builders = dict(mapping)

    def order_builder_for(self, workflow_name: str | None) -> OrderBuilder:
        """Resolve OrderBuilder for workflow_name; raise ConfigError if none configured."""
        if workflow_name and workflow_name in self._order_builders:
            return self._order_builders[workflow_name]
        if self._order_builder is not None:
            return self._order_builder
        raise ConfigError(
            "No order builder configured",
            code="ORDER_BUILDER_MISSING",
            hint="pass order_builder or order_builders",
        )

    def check_idempotency(self, key: str) -> bool:
        return exec_jsonl.check_idempotency(self._exec_jsonl_path, key)

    def get_generation(
        self,
        workflow_name: str,
        handler_name: str,
        issue_number: int,
    ) -> int:
        return exec_jsonl.get_generation(
            self._state_dir, workflow_name, handler_name, issue_number,
        )

    def increment_generation(
        self,
        workflow_name: str,
        handler_name: str,
        issue_number: int,
    ) -> int:
        key = exec_jsonl.handler_generation_key(workflow_name, handler_name, issue_number)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        with open(self._generations_path, "a+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                raw = f.read()
                data: dict[str, int] = json.loads(raw) if raw.strip() else {}
                current = int(data.get(key, 0)) if isinstance(data.get(key, 0), int) else 0
                new_value = current + 1
                data[key] = new_value
                fd, tmp = tempfile.mkstemp(dir=str(self._state_dir), suffix=".tmp")
                try:
                    with os.fdopen(fd, "w") as tf:
                        json.dump(data, tf, ensure_ascii=False, indent=2)
                        tf.write("\n")
                    os.replace(tmp, str(self._generations_path))
                except BaseException:
                    with contextlib.suppress(OSError):
                        os.unlink(tmp)
                    raise
                return new_value
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def find_records_by_idempotency_key(self, key: str) -> list[dict]:
        return exec_jsonl.find_records_by_idempotency_key(self._exec_jsonl_path, key)

    def remove_idempotency_matching(self, workflow_name: str, issue_number: int) -> int:
        def should_remove(rec: dict) -> bool:
            parsed = exec_jsonl.parse_idempotency_key(rec.get("idempotency_key"))
            if parsed is None:
                return False
            wf, _handler, issue, _gen = parsed
            return wf == workflow_name and issue == issue_number

        return exec_jsonl.remove_by_predicate(self._exec_jsonl_path, should_remove)

    def remove_idempotency_for_handler(
        self,
        workflow_name: str,
        handler_name: str,
        issue_number: int,
    ) -> int:
        def should_remove(rec: dict) -> bool:
            parsed = exec_jsonl.parse_idempotency_key(rec.get("idempotency_key"))
            if parsed is None:
                return False
            wf, handler, issue, _gen = parsed
            return (
                wf == workflow_name
                and handler == handler_name
                and issue == issue_number
            )

        return exec_jsonl.remove_by_predicate(self._exec_jsonl_path, should_remove)

    def enqueue_records(
        self,
        records: list[dict],
        audit_context: AuditContext | None = None,
    ) -> None:
        exec_jsonl.append(
            self._exec_jsonl_path,
            records,
            audit_context or AuditContext(),
            audit_path=self._exec_jsonl_path.parent / "audit.jsonl",
            quota_gate=self._quota_gate,
        )

    def write_order(
        self,
        ts: str,
        order_uuid: str,
        content: str,
        queue_dir: str,
        engine: str = "claude",
        order_footer_fn: Callable[[str, str, str], str] | None = None,
    ) -> str:
        if not engine:
            raise ValueError("engine must not be empty")
        if order_footer_fn is not None:
            content = content + order_footer_fn(ts, order_uuid, engine)
        filename = f"{ts}-{engine}-order-{order_uuid}.md"
        path = os.path.join(queue_dir, filename)
        with open(path, "a+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                f.truncate()
                f.write(content)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return filename

    def enqueue(
        self,
        steps: list[StepConfig],
        base_context: dict[str, str],
        *,
        idempotency_key: str | None = None,
        audit_context: AuditContext,
        metadata: dict[str, str] | None = None,
        order_builder: OrderBuilder | None = None,
    ) -> list[str]:
        validate_depends(steps)

        ts = file_ts(self._timezone)
        if order_builder is None:
            order_builder = self.order_builder_for(base_context.get("workflow_name"))
        step_uuid_map: dict[str, str] = {}
        step_engine_map: dict[str, str] = {}

        return self._enqueue_jsonl(
            steps,
            base_context,
            idempotency_key,
            ts,
            order_builder,
            step_uuid_map,
            step_engine_map,
            audit_context,
            metadata=metadata,
        )

    def _enqueue_jsonl(
        self,
        steps: list[StepConfig],
        base_context: dict[str, str],
        idempotency_key: str | None,
        ts: str,
        order_builder: OrderBuilder,
        step_uuid_map: dict[str, str],
        step_engine_map: dict[str, str],
        audit_context: AuditContext,
        *,
        metadata: dict[str, str] | None = None,
    ) -> list[str]:
        records: list[dict] = []

        step_uuid_list: list[str] = []
        for step in steps:
            step_uuid = str(uuid.uuid4())
            engine = step.engine
            step_id = step.id if step.id else step_uuid
            step_uuid_map[step_id] = step_uuid
            step_engine_map[step_id] = engine
            step_uuid_list.append(step_uuid)

        for step, step_uuid in zip(steps, step_uuid_list, strict=True):
            engine = step.engine
            step_id = step.id if step.id else step_uuid

            result_filename = f"{ts}-{engine}-result-{step_uuid}.md"
            context = dict(base_context)
            context.update({
                "ts": ts,
                "order_uuid": step_uuid,
                "result_uuid": step_uuid,
                "result_filename": result_filename,
            })
            for dep_id in step.depends:
                dep_uuid = step_uuid_map[dep_id]
                dep_engine = step_engine_map[dep_id]
                dep_result_filename = f"{ts}-{dep_engine}-result-{dep_uuid}.md"
                context[f"{dep_id}_result_filename"] = dep_result_filename

                dep_result_path = os.path.join(self._queue_dir, dep_result_filename)
                if os.path.isfile(dep_result_path):
                    with open(dep_result_path, encoding="utf-8") as f:
                        context[f"{dep_id}_result_content"] = f.read()
                else:
                    context[f"{dep_id}_result_content"] = ""

            order_content = order_builder.build_order(step.template, context)
            order_filename = self.write_order(
                ts, step_uuid, order_content, self._queue_dir, engine=engine,
            )
            record = self._build_exec_record(
                step_uuid=step_uuid,
                depends=[step_uuid_map[d] for d in step.depends],
                order_filename=order_filename,
                result_filename=result_filename,
                engine=engine,
                model=step.model,
                permission=step.permission,
            )
            record.setdefault("annotations", {})["step_name"] = step_id
            if step.resume_from:
                record.setdefault("annotations", {})["resume_from_uuid"] = step_uuid_map[
                    step.resume_from
                ]
            if step.env_passthrough:
                record.setdefault("annotations", {})["env_passthrough"] = step.env_passthrough
            if metadata:
                record.setdefault("annotations", {}).update(metadata)
            if idempotency_key:
                record["idempotency_key"] = idempotency_key
            records.append(record)

        self.enqueue_records(records, audit_context=audit_context)
        return [json.dumps(r, ensure_ascii=False) for r in records]

    def _build_exec_record(
        self,
        *,
        step_uuid: str,
        depends: list[str],
        order_filename: str,
        result_filename: str,
        engine: str,
        model: str,
        permission: str | None = None,
    ) -> dict:
        from ghdag.core.capabilities import PERMISSIONS
        from ghdag.core.command import render_exec_command
        from ghdag.core.engine_spec import ENGINE_SPECS

        preset_keys = sorted(PERMISSIONS.keys())
        if permission is not None and permission not in PERMISSIONS:
            raise ConfigError(
                f"Unknown permission preset: {permission!r}. Available: {preset_keys}",
                code="STEP_PERMISSION_UNKNOWN",
                hint=f"available presets: {preset_keys}",
            )

        safe_default_env: str | None = None
        safe_default_applied = False
        if permission is not None:
            perm = PERMISSIONS[permission]
        else:
            safe_default_env = ghdag_safe_default_permission()
            if safe_default_env:
                if safe_default_env not in PERMISSIONS:
                    raise ConfigError(
                        f"Unknown GHDAG_SAFE_DEFAULT_PERMISSION: {safe_default_env!r}. "
                        f"Available: {preset_keys}",
                        code="STEP_PERMISSION_UNKNOWN",
                        hint=f"available presets: {preset_keys}",
                    )
                perm = PERMISSIONS[safe_default_env]
                safe_default_applied = True
            else:
                safe_default_env = "text_only"
                perm = PERMISSIONS["text_only"]
                safe_default_applied = True

        if engine not in ENGINE_SPECS:
            from ghdag.llm.engines import EngineModelError

            available = sorted(ENGINE_SPECS.keys())
            raise EngineModelError(
                f"Unknown engine: {engine!r}. Available: {available}",
                code="ENGINE_UNKNOWN",
                hint=f"available engines: {available}",
            )
        spec = ENGINE_SPECS[engine]
        annotations: dict[str, object] = {}
        if permission is None and safe_default_applied:
            annotations["safe_default_applied"] = True
            annotations["safe_default_preset"] = safe_default_env

        return {
            "uuid": step_uuid,
            "engine": spec.name,
            "model": model if spec.model_flag else None,
            "command": render_exec_command(
                spec,
                order_path=f"{self._queue_dir}/{order_filename}",
                model=model,
                permission=perm,
                isolation=engine_isolation_enabled(),
            ),
            "depends": depends,
            "result_path": f"{self._queue_dir}/{result_filename}",
            "retry": 0,
            "annotations": annotations,
        }
