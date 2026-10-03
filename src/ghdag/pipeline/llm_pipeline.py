"""
pipeline/llm_pipeline.py — LLMPipelineAPI: handles order/result/exec.jsonl submission in one place

The dispatcher only calls submit() and does not need to know file naming
conventions or the exec line format.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from ghdag.config.env import ghdag_safe_default_permission
from ghdag.exceptions import GhdagError
from ghdag.pipeline.audit import AuditContext
from ghdag.pipeline.order import OrderBuilder
from ghdag.pipeline.state import PipelineState


class DependencyError(GhdagError, ValueError):
    """Raised when step dependencies are invalid or circular."""

if TYPE_CHECKING:
    from ghdag.workflow.schema import StepConfig


def _validate_depends(steps: "list[StepConfig]") -> None:
    """Pre-validate depends: detect undefined references and cycles.

    Raises:
        ValueError: an undefined dep_id exists, or there is a cycle
    """
    step_ids = {s.id for s in steps if s.id is not None}

    for step in steps:
        for dep_id in step.depends:
            if dep_id not in step_ids:
                raise DependencyError(f"Unknown dependency: {dep_id!r}")

    # Detect cycles via topological sort
    in_degree: dict[str, int] = {s.id: 0 for s in steps if s.id is not None}
    adjacency: dict[str, list[str]] = {s.id: [] for s in steps if s.id is not None}

    for step in steps:
        if step.id is None:
            continue
        for dep_id in step.depends:
            adjacency[dep_id].append(step.id)
            in_degree[step.id] += 1

    queue = [sid for sid, deg in in_degree.items() if deg == 0]
    visited = 0
    while queue:
        node = queue.pop(0)
        visited += 1
        for neighbor in adjacency[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited < len(in_degree):
        raise DependencyError("circular dependency detected among steps")

    dep_map: dict[str, set[str]] = {
        s.id: set(s.depends) for s in steps if s.id is not None
    }

    def _is_transitive_ancestor(child_id: str, ancestor_id: str) -> bool:
        seen: set[str] = set()
        queue = list(dep_map.get(child_id, set()))
        while queue:
            dep = queue.pop(0)
            if dep == ancestor_id:
                return True
            if dep in seen:
                continue
            seen.add(dep)
            queue.extend(dep_map.get(dep, set()))
        return False

    for step in steps:
        resume_from = step.resume_from
        if resume_from is None:
            continue
        if not isinstance(resume_from, str):
            raise DependencyError(
                f"resume_from must be a single step id string (fan-in is unsupported): {resume_from!r}"
            )
        if step.id is None:
            raise DependencyError("resume_from requires step.id to be set")
        if resume_from not in step_ids:
            raise DependencyError(f"Unknown resume_from dependency: {resume_from!r}")
        if step.id == resume_from:
            raise DependencyError("resume_from cannot reference itself")
        if not _is_transitive_ancestor(step.id, resume_from):
            raise DependencyError(
                f"resume_from {resume_from!r} must be a transitive ancestor of step {step.id!r}"
            )


@dataclass
class SubmittedStep:
    """Information about a submitted step, included in the return value of submit()."""
    step_id: str
    uuid: str
    order_filename: str
    result_filename: str
    exec_line: str


class LLMPipelineAPI:
    """Handles order/result file management and exec.jsonl submission in one place.

    The dispatcher only calls submit() and does not need to know file naming
    conventions or the exec line format.
    """

    def __init__(
        self,
        pipeline_state: PipelineState,
        order_builder: OrderBuilder,
        queue_dir: str = "queue",
        *,
        order_builders: dict[str, OrderBuilder] | None = None,
    ):
        """
        Args:
            pipeline_state: PipelineState instance
            order_builder: default OrderBuilder.
                Fallback used when base_context["workflow_name"] is not in
                ``order_builders``, or when ``order_builders`` is None.
            queue_dir: directory where order/result files are written
            order_builders: map of workflow_name → OrderBuilder.
                A dispatcher spanning multiple workflows (``ghdag watch``) may
                have a different ``template_dir`` per workflow, so pass
                per-workflow OrderBuilders here to switch template resolution
                on a per-workflow basis.
                Single-workflow callers (``ghdag trigger``) can leave it
                as ``None``.
        """
        self._state = pipeline_state
        self._order_builder = order_builder
        self._queue_dir = queue_dir
        self._order_builders: dict[str, OrderBuilder] = dict(order_builders or {})

    def check_idempotency(self, key: str) -> bool:
        """Delegate the idempotency check to PipelineState."""
        return self._state.check_idempotency(key)

    def get_generation(
        self, workflow_name: str, handler_name: str, issue_number: int,
    ) -> int:
        """Return the current redispatch generation."""
        return self._state.get_generation(workflow_name, handler_name, issue_number)

    def increment_generation(
        self, workflow_name: str, handler_name: str, issue_number: int,
    ) -> int:
        """Increment the redispatch generation by 1 and return it."""
        return self._state.increment_generation(workflow_name, handler_name, issue_number)

    def find_records_by_idempotency_key(self, key: str) -> list[dict]:
        """Return the exec.jsonl record matching the idempotency key."""
        return self._state.find_records_by_idempotency_key(key)

    def remove_idempotency_matching(self, workflow_name: str, issue_number: int) -> None:
        """Delegate idempotency key removal to PipelineState."""
        self._state.remove_idempotency_matching(workflow_name, issue_number)

    def remove_idempotency_for_handler(
        self, workflow_name: str, handler_name: str, issue_number: int
    ) -> int:
        """Delegate per-handler idempotency key removal to PipelineState."""
        return self._state.remove_idempotency_for_handler(workflow_name, handler_name, issue_number)

    def submit(
        self,
        steps: list[StepConfig],
        base_context: dict[str, str],
        *,
        idempotency_key: str | None = None,
        audit_context: AuditContext,
        metadata: dict[str, str] | None = None,
        order_builder: OrderBuilder | None = None,
        workflow_roles: dict[str, list[str]] | None = None,
    ) -> list[str]:
        """Submit steps to order/exec.jsonl files.

        Args:
            steps: list of StepConfig to run
            base_context: context variables shared by all steps
            idempotency_key: idempotency key (not recorded when omitted)
            audit_context: context recorded in the enqueue audit
            metadata: metadata shared by all steps (stored in exec.jsonl annotations)
            order_builder: if given, used instead of `_resolve_order_builder`
            workflow_roles: role name → list of engine names (for step.role annotations)

        Returns:
            list of written JSON records as strings (for DispatchResult)
        """
        _validate_depends(steps)

        ts = datetime.now(tz=ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d%H%M%S")
        if order_builder is None:
            order_builder = self._resolve_order_builder(base_context.get("workflow_name"))
        step_uuid_map: dict[str, str] = {}
        step_engine_map: dict[str, str] = {}

        return self._submit_jsonl(
            steps, base_context, idempotency_key, ts, order_builder,
            step_uuid_map, step_engine_map, audit_context,
            metadata=metadata,
            workflow_roles=workflow_roles,
        )

    def _submit_jsonl(
        self,
        steps: "list[StepConfig]",
        base_context: dict[str, str],
        idempotency_key: str | None,
        ts: str,
        order_builder: OrderBuilder,
        step_uuid_map: dict[str, str],
        step_engine_map: dict[str, str],
        audit_context: AuditContext,
        *,
        metadata: dict[str, str] | None = None,
        workflow_roles: dict[str, list[str]] | None = None,
    ) -> list[str]:
        """Write in JSONL format (exec.jsonl)."""
        import json as _json

        records: list[dict] = []

        for step in steps:
            step_uuid = str(uuid.uuid4())
            engine = step.engine
            step_id = step.id if step.id else step_uuid
            step_uuid_map[step_id] = step_uuid
            step_engine_map[step_id] = engine

            result_filename = f"{ts}-{engine}-result-{step_uuid}.md"
            context = dict(base_context)
            context.update({
                "ts": ts,
                "order_uuid": step_uuid,
                "result_uuid": step_uuid,
                "result_filename": result_filename,
            })
            for dep_id in step.depends:
                if dep_id in step_uuid_map:
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
            order_filename = self._state.write_order_file(
                ts, step_uuid, order_content, self._queue_dir, engine=engine
            )
            record = self._build_exec_record(
                step_uuid=step_uuid,
                depends=[step_uuid_map[d] for d in step.depends if d in step_uuid_map],
                order_filename=order_filename,
                result_filename=result_filename,
                engine=engine,
                model=step.model,
                permission=step.permission,
            )
            record.setdefault("annotations", {})["step_name"] = step_id
            if step.resume_from:
                record.setdefault("annotations", {})["resume_from_uuid"] = step_uuid_map[step.resume_from]
            if step.role is not None:
                role_engines = list((workflow_roles or {}).get(step.role, []))
                record.setdefault("annotations", {})["role"] = step.role
                record.setdefault("annotations", {})["role_engines"] = role_engines
            if metadata:
                record.setdefault("annotations", {}).update(metadata)
            if idempotency_key:
                record["idempotency_key"] = idempotency_key
            records.append(record)

        self._state.append_exec_records(records, audit_context=audit_context)
        return [_json.dumps(r, ensure_ascii=False) for r in records]

    def _resolve_order_builder(self, workflow_name: str | None) -> OrderBuilder:
        """Resolve the OrderBuilder from workflow_name.

        Returns the matching entry in ``order_builders`` if present, otherwise
        the (default) ``order_builder``.
        """
        if workflow_name and workflow_name in self._order_builders:
            return self._order_builders[workflow_name]
        return self._order_builder

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
        """Build one exec.jsonl record (internal method)."""
        from ghdag.llm.capabilities import PRESETS
        from ghdag.llm.spec import ENGINE_SPECS, render_exec_command

        if permission is not None and permission not in PRESETS:
            raise ValueError(
                f"Unknown permission preset: {permission!r}. "
                f"Available: {sorted(PRESETS.keys())}"
            )

        safe_default_env: str | None = None
        safe_default_applied = False
        if permission is not None:
            capabilities = PRESETS[permission]
        else:
            safe_default_env = ghdag_safe_default_permission()
            if safe_default_env:
                if safe_default_env not in PRESETS:
                    raise ValueError(
                        f"Unknown GHDAG_SAFE_DEFAULT_PERMISSION: {safe_default_env!r}. "
                        f"Available: {sorted(PRESETS.keys())}"
                    )
                capabilities = PRESETS[safe_default_env]
                safe_default_applied = True
            else:
                safe_default_env = "text_only"  # safe default (hardcoded)
                capabilities = PRESETS["text_only"]
                safe_default_applied = True

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
                capabilities=capabilities,
                isolation=bool(os.environ.get("GHDAG_ENGINE_ISOLATION")),
            ),
            "depends": depends,
            "result_path": f"{self._queue_dir}/{result_filename}",
            "retry": 0,
            "annotations": annotations,
        }
