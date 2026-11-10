"""MCP tool definitions and implementations."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ghdag.core.capabilities import PRESETS
from ghdag.core.command import get_adapter
from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.core.exceptions import ConfigError
from ghdag.core.models.workflow import StepConfig
from ghdag.io import exec_jsonl
from ghdag.io.done import dep_succeeded, done_failure_class, interpret_done, read_done_content
from ghdag.llm.engines import get_engine_models, supports_capability
from ghdag.pipeline.audit import AuditContext
from ghdag.pipeline.order import InlineOrderBuilder
from ghdag.pipeline.state import PipelineState
from ghdag.schemas import SCHEMA_NAMES
from ghdag.status import issue_status, running_tasks

_TZ = ZoneInfo("Asia/Tokyo")

try:
    _PKG_VERSION = version("ghdag")
except PackageNotFoundError:
    _PKG_VERSION = "unknown"

TOOL_NAMES: tuple[str, ...] = (
    "ghdag_enqueue_task",
    "ghdag_enqueue_dag",
    "ghdag_get_status",
    "ghdag_describe",
)

_TASK_KEYS = frozenset(
    {"engine", "prompt", "command", "model", "depends", "permission", "correlation_id"}
)
_DAG_STEP_KEYS = frozenset(
    {"id", "engine", "prompt", "command", "model", "depends", "permission"}
)
_STATUS_ISSUE_KEYS = frozenset({"issue", "handler", "workflow"})


def _schema_object(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    examples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    if examples:
        schema["examples"] = examples
    return schema


def _enqueue_task_schema() -> dict[str, Any]:
    engine_enum = sorted(ENGINE_SPECS)
    perm_enum = sorted(PRESETS)
    return _schema_object(
        {
            "engine": {
                "type": "string",
                "enum": engine_enum,
                "description": "Execution engine name",
            },
            "prompt": {
                "type": "string",
                "description": "Order body for LLM engines (mutually exclusive with command)",
            },
            "command": {
                "type": "string",
                "description": "Shell command body when engine is shell",
            },
            "model": {
                "type": "string",
                "description": "Model id (defaults to engine default)",
            },
            "depends": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Dependency task UUIDs already present in exec.jsonl",
            },
            "permission": {
                "type": "string",
                "enum": perm_enum,
                "description": "Capabilities preset",
            },
            "correlation_id": {
                "type": "string",
                "description": "Correlation id recorded in the enqueue audit log",
            },
        },
        required=["engine"],
        examples=[{"engine": "shell", "command": "echo hi"}],
    )


def _enqueue_dag_schema() -> dict[str, Any]:
    engine_enum = sorted(ENGINE_SPECS)
    perm_enum = sorted(PRESETS)
    step_schema = _schema_object(
        {
            "id": {"type": "string", "description": "Step id referenced by depends"},
            "engine": {
                "type": "string",
                "enum": engine_enum,
                "description": "Execution engine",
            },
            "prompt": {"type": "string", "description": "Prompt body for LLM engines"},
            "command": {"type": "string", "description": "Command body for shell"},
            "model": {"type": "string", "description": "Model id"},
            "depends": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Sibling step ids this step depends on",
            },
            "permission": {
                "type": "string",
                "enum": perm_enum,
                "description": "Capabilities preset",
            },
        },
        required=["id", "engine"],
    )
    return _schema_object(
        {
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": step_schema,
                "description": "DAG steps to enqueue atomically",
            },
            "correlation_id": {
                "type": "string",
                "description": "Correlation id for the enqueue audit log",
            },
        },
        required=["steps"],
        examples=[
            {
                "steps": [
                    {"id": "a", "engine": "shell", "command": "echo a"},
                    {"id": "b", "engine": "shell", "command": "echo b", "depends": ["a"]},
                ]
            }
        ],
    )


def _get_status_schema() -> dict[str, Any]:
    issue_props: dict[str, Any] = {
        k: {"type": "string", "description": f"Issue filter: {k}"}
        for k in ("handler", "workflow")
    }
    issue_props["issue"] = {"type": "integer", "description": "GitHub issue number"}
    props = {
        "uuids": {
            "type": "array",
            "minItems": 1,
            "maxItems": 100,
            "items": {"type": "string"},
            "description": "Task UUIDs to inspect",
        },
        **issue_props,
    }
    return _schema_object(
        props,
        examples=[{"uuids": ["00000000-0000-0000-0000-000000000001"]}],
    )


def _describe_schema() -> dict[str, Any]:
    return _schema_object({}, examples=[{}])


_MCP_ISSUE_STATUS = frozenset(
    {"success", "failed", "pending", "running", "skipped", "cancelled", "dep_failed"}
)
_MCP_COLLAPSE_FAILED = frozenset(
    {"rejected", "empty_result", "engine_error", "other", "failed_exit"}
)


def _collapse_task_status(core: str) -> str:
    if core in _MCP_ISSUE_STATUS:
        return core
    if core in _MCP_COLLAPSE_FAILED:
        return "failed"
    if core == "deferred":
        return "pending"
    return "failed"


def _mcp_core_status(
    uuid: str,
    done_dir: Path,
    *,
    depends: list[str],
    running: set[str],
) -> str:
    if uuid in running:
        return "running"
    raw = read_done_content(done_dir, uuid)
    if raw is not None:
        kind = interpret_done(raw)
        if kind == "success":
            return "success"
        if kind is not None:
            if kind == "failed_exit":
                return "failed_exit"
            return kind
    for dep in depends:
        if dep in running:
            return "pending"
        dep_raw = read_done_content(done_dir, dep)
        if dep_raw is None:
            return "pending"
        if interpret_done(dep_raw) != "success":
            return "pending"
        if not dep_succeeded(done_dir, dep):
            return "pending"
    return "pending"


def _mcp_done_fields(raw: str | None, *, status: str) -> tuple[
    str | None, str | None, str | None, str | None
]:
    if status in ("pending", "running") or raw is None:
        return None, None, None, None
    lines = raw.strip().splitlines()
    done_value = lines[0].strip() if lines else None
    failure = done_failure_class(raw)
    if failure is None:
        return done_value, None, None, None
    return done_value, failure.value, failure.cause, failure.retry_policy


def _validate_mcp_depends(steps: list[StepConfig]) -> None:
    """Same rules as pipeline.validate_depends for MCP DAG enqueue."""
    step_ids = {s.id for s in steps if s.id is not None}

    for step in steps:
        for dep_id in step.depends:
            if dep_id not in step_ids:
                raise ConfigError(
                    f"Unknown dependency: {dep_id!r}",
                    code="STEP_DEPENDENCY_UNKNOWN",
                    hint=f"defined step ids: {sorted(step_ids)}",
                )

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
        raise ConfigError(
            "circular dependency detected among steps",
            code="STEP_DEPENDENCY_CYCLE",
        )


_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "ghdag_enqueue_task",
        "description": "Enqueue a single exec.jsonl task with a new order file",
        "inputSchema": _enqueue_task_schema(),
    },
    {
        "name": "ghdag_enqueue_dag",
        "description": "Enqueue multiple dependent steps in one exec.jsonl append",
        "inputSchema": _enqueue_dag_schema(),
    },
    {
        "name": "ghdag_get_status",
        "description": "Read task status by uuid or Issue handler workflow",
        "inputSchema": _get_status_schema(),
    },
    {
        "name": "ghdag_describe",
        "description": "Describe engines, permissions, schemas, and queue paths",
        "inputSchema": _describe_schema(),
    },
]


@dataclass
class McpRuntime:
    """Paths and pipeline handles shared by MCP tools."""

    state: PipelineState
    pipeline: Any
    queue_dir: str
    done_dir: Path
    running_dir: Path


class McpToolRegistry:
    """Registry of MCP tools backed by pipeline state."""

    def __init__(self, runtime: McpRuntime) -> None:
        self._runtime = runtime

    def list_tools(self) -> list[dict[str, Any]]:
        return [dict(spec) for spec in _TOOL_SPECS]

    def call_tool(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        if arguments is None:
            arguments = {}
        if name == "ghdag_enqueue_task":
            return self._enqueue_task(arguments)
        if name == "ghdag_enqueue_dag":
            return self._enqueue_dag(arguments)
        if name == "ghdag_get_status":
            return self._get_status(arguments)
        if name == "ghdag_describe":
            return self._describe(arguments)
        raise KeyError(name)

    def _mcp_invalid(self, hint: str) -> ConfigError:
        return ConfigError(
            "Invalid MCP tool arguments",
            code="MCP_ARGUMENT_INVALID",
            hint=hint,
        )

    def _validate_keys(self, args: dict[str, Any], allowed: frozenset[str]) -> None:
        extra = set(args) - allowed
        if extra:
            raise self._mcp_invalid(f"remove unknown keys: {sorted(extra)}")

    def _validate_engine_body(
        self,
        engine: str,
        *,
        prompt: str | None,
        command: str | None,
    ) -> str:
        if engine == "shell":
            if prompt is not None:
                raise self._mcp_invalid("shell tasks require command, not prompt")
            if not command:
                raise self._mcp_invalid("provide command for shell engine")
            return command
        if command is not None:
            raise self._mcp_invalid("LLM engines require prompt, not command")
        if not prompt:
            raise self._mcp_invalid("provide prompt for non-shell engine")
        return prompt

    def _known_uuids(self) -> set[str]:
        path = Path(self._runtime.state._exec_jsonl_path)
        if not path.is_file():
            return set()
        return {t.uuid for t in exec_jsonl.parse(exec_jsonl.read(path))}

    def _enqueue_task(self, args: dict[str, Any]) -> dict[str, Any]:
        self._validate_keys(args, _TASK_KEYS)
        engine = args.get("engine")
        if not isinstance(engine, str):
            raise self._mcp_invalid("engine must be a string")
        if engine not in ENGINE_SPECS:
            available = sorted(ENGINE_SPECS)
            raise ConfigError(
                f"Unknown engine: {engine!r}",
                code="ENGINE_UNKNOWN",
                hint=f"available engines: {available}",
            )

        prompt = args.get("prompt")
        command = args.get("command")
        if prompt is not None and not isinstance(prompt, str):
            raise self._mcp_invalid("prompt must be a string")
        if command is not None and not isinstance(command, str):
            raise self._mcp_invalid("command must be a string")
        content = self._validate_engine_body(
            engine,
            prompt=prompt if isinstance(prompt, str) else None,
            command=command if isinstance(command, str) else None,
        )

        depends = args.get("depends", [])
        if depends is None:
            depends = []
        if not isinstance(depends, list) or not all(isinstance(d, str) for d in depends):
            raise self._mcp_invalid("depends must be an array of uuid strings")

        permission = args.get("permission")
        if permission is not None and permission not in PRESETS:
            raise self._mcp_invalid(f"permission must be one of {sorted(PRESETS)}")

        known = self._known_uuids()
        for dep in depends:
            if dep not in known:
                raise ConfigError(
                    f"Unknown dependency uuid: {dep!r}",
                    code="DEPENDENCY_UUID_UNKNOWN",
                    hint="enqueue the dependency first or drop it",
                )

        model = args.get("model")
        if model is not None and not isinstance(model, str):
            raise self._mcp_invalid("model must be a string")
        spec = ENGINE_SPECS[engine]
        if model is None and spec.default_model:
            model = spec.default_model

        preset_key = permission or "text_only"
        capabilities = PRESETS[preset_key]

        ts = datetime.now(_TZ).strftime("%Y%m%d%H%M%S")
        uid = str(uuid.uuid4())
        state = self._runtime.state
        queue_dir = self._runtime.queue_dir

        order_filename = state.write_order_file(ts, uid, content, queue_dir, engine=engine)
        order_path = f"{queue_dir}/{order_filename}"
        result_path = f"{queue_dir}/{ts}-{engine}-result-{uid}.md"

        record = get_adapter(engine).build_exec_record(
            uuid=uid,
            order_path=order_path,
            result_path=result_path,
            model=model,
            depends=list(depends),
            capabilities=capabilities,
        )
        annotations = record.setdefault("annotations", {})
        annotations["step_name"] = "mcp"
        if permission is None:
            annotations["safe_default_preset"] = preset_key

        correlation_id = args.get("correlation_id")
        if correlation_id is not None and not isinstance(correlation_id, str):
            raise self._mcp_invalid("correlation_id must be a string")
        audit_ctx = AuditContext(
            source="mcp",
            correlation_id=correlation_id if correlation_id else uid,
        )
        state.append_exec_records([record], audit_context=audit_ctx)

        return {
            "uuid": uid,
            "order_path": order_path,
            "result_path": result_path,
        }

    def _enqueue_dag(self, args: dict[str, Any]) -> dict[str, Any]:
        self._validate_keys(args, frozenset({"steps", "correlation_id"}))
        steps_raw = args.get("steps")
        if not isinstance(steps_raw, list):
            raise self._mcp_invalid("steps must be an array")
        if not (1 <= len(steps_raw) <= 20):
            raise self._mcp_invalid("steps must contain between 1 and 20 items")

        step_configs: list[StepConfig] = []
        step_ids: set[str] = set()
        for raw in steps_raw:
            if not isinstance(raw, dict):
                raise self._mcp_invalid("each step must be an object")
            self._validate_keys(raw, _DAG_STEP_KEYS)
            step_id = raw.get("id")
            if not isinstance(step_id, str) or not step_id:
                raise self._mcp_invalid("each step needs a non-empty id string")
            if step_id in step_ids:
                raise self._mcp_invalid(f"step ids must be unique: {step_id!r}")
            step_ids.add(step_id)
            engine = raw.get("engine")
            if not isinstance(engine, str) or engine not in ENGINE_SPECS:
                available = sorted(ENGINE_SPECS)
                raise ConfigError(
                    f"Unknown engine: {engine!r}",
                    code="ENGINE_UNKNOWN",
                    hint=f"available engines: {available}",
                )
            prompt = raw.get("prompt")
            command = raw.get("command")
            if prompt is not None and not isinstance(prompt, str):
                raise self._mcp_invalid("prompt must be a string")
            if command is not None and not isinstance(command, str):
                raise self._mcp_invalid("command must be a string")
            body = self._validate_engine_body(
                engine,
                prompt=prompt,
                command=command,
            )
            if engine != "shell":
                body = body.replace("$", "$$")
            depends = raw.get("depends", [])
            if depends is None:
                depends = []
            if not isinstance(depends, list) or not all(isinstance(d, str) for d in depends):
                raise self._mcp_invalid("depends must be an array of step id strings")
            permission = raw.get("permission")
            if permission is not None and permission not in PRESETS:
                raise self._mcp_invalid(f"permission must be one of {sorted(PRESETS)}")
            model = raw.get("model")
            if model is not None and not isinstance(model, str):
                raise self._mcp_invalid("model must be a string")
            if model is None:
                spec = ENGINE_SPECS[engine]
                model = spec.default_model
            step_configs.append(
                StepConfig(
                    id=step_id,
                    template=body,
                    engine=engine,
                    model=model,
                    depends=list(depends),
                    permission=permission,
                )
            )

        correlation_id = args.get("correlation_id")
        if correlation_id is not None and not isinstance(correlation_id, str):
            raise self._mcp_invalid("correlation_id must be a string")

        _validate_mcp_depends(step_configs)

        lines = self._runtime.pipeline.submit(
            step_configs,
            {},
            audit_context=AuditContext(
                source="mcp",
                correlation_id=correlation_id or str(uuid.uuid4()),
            ),
            order_builder=InlineOrderBuilder(),
        )
        uuids: dict[str, str] = {}
        for line in lines:
            data = json.loads(line)
            sid = (data.get("annotations") or {}).get("step_name")
            if isinstance(sid, str) and isinstance(data.get("uuid"), str):
                uuids[sid] = data["uuid"]
        return {"uuids": uuids}

    def _running_uuid_set(self) -> set[str]:
        directory = self._runtime.running_dir
        if not directory.is_dir():
            return set()
        return {p.stem for p in directory.glob("*.json")}

    def _task_records(self) -> dict[str, dict[str, Any]]:
        path = Path(self._runtime.state._exec_jsonl_path)
        if not path.is_file():
            return {}
        out: dict[str, dict[str, Any]] = {}
        text = exec_jsonl.read(path)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data.get("uuid"), str):
                out[data["uuid"]] = data
        return out

    def _get_status(self, args: dict[str, Any]) -> dict[str, Any]:
        allowed = _STATUS_ISSUE_KEYS | frozenset({"uuids"})
        self._validate_keys(args, allowed)
        has_uuids = "uuids" in args
        has_issue = any(k in args for k in _STATUS_ISSUE_KEYS)
        if has_uuids and has_issue:
            raise self._mcp_invalid("use either uuids or issue/handler/workflow, not both")
        if not has_uuids and not has_issue:
            raise self._mcp_invalid("provide uuids or issue, handler, and workflow")

        if has_issue:
            issue = args.get("issue")
            handler = args.get("handler")
            workflow = args.get("workflow")
            if not isinstance(issue, int):
                raise self._mcp_invalid("issue must be an integer")
            if not isinstance(handler, str) or not isinstance(workflow, str):
                raise self._mcp_invalid("handler and workflow must be strings")
            running = self._running_uuid_set()
            status = issue_status(
                issue,
                handler=handler,
                workflow=workflow,
                exec_jsonl_path=self._runtime.state._exec_jsonl_path,
                state_dir=self._runtime.state._state_dir,
                done_dir=self._runtime.done_dir,
                running_uuids=running,
                running_dir=self._runtime.running_dir,
            )
            payload = {
                "generation": status.generation,
                "running": status.running,
                "orphan_uuids": status.orphan_uuids,
                "steps": [
                    {
                        "uuid": s.uuid,
                        "step_name": s.step_name,
                        "depends": s.depends,
                        "status": s.status,
                        "started_at": s.started_at,
                        "elapsed_sec": s.elapsed_sec,
                        "result_path": s.result_path,
                        "done_value": s.done_value,
                        "failure_class": s.failure_class,
                        "cause": s.cause,
                        "retry_policy": s.retry_policy,
                    }
                    for s in status.steps
                ],
                "running_tasks": [
                    {
                        "uuid": t.uuid,
                        "started_at": t.started_at,
                        "elapsed_sec": t.elapsed_sec,
                        "engine": t.engine,
                        "pid": t.pid,
                    }
                    for t in running_tasks(self._runtime.running_dir)
                ],
            }
            return payload

        uuids = args["uuids"]
        if not isinstance(uuids, list) or not all(isinstance(u, str) for u in uuids):
            raise self._mcp_invalid("uuids must be an array of strings")
        if not (1 <= len(uuids) <= 100):
            raise self._mcp_invalid("uuids length must be between 1 and 100")

        records = self._task_records()
        running = self._running_uuid_set()
        tasks = []
        for uid in uuids:
            record = records.get(uid)
            depends = list(record.get("depends", [])) if record else []
            core = _mcp_core_status(
                uid,
                self._runtime.done_dir,
                depends=depends,
                running=running,
            )
            status = _collapse_task_status(core)
            done_raw = read_done_content(self._runtime.done_dir, uid)
            done_value, failure_class, cause, retry_policy = _mcp_done_fields(
                done_raw,
                status=status,
            )
            tasks.append(
                {
                    "uuid": uid,
                    "status": status,
                    "done_value": done_value,
                    "failure_class": failure_class,
                    "cause": cause,
                    "retry_policy": retry_policy,
                    "result_path": record.get("result_path") if record else None,
                }
            )
        return {"tasks": tasks}

    def _describe(self, args: dict[str, Any]) -> dict[str, Any]:
        self._validate_keys(args, frozenset())
        models_map = get_engine_models()
        engines: dict[str, Any] = {}
        for name, spec in sorted(ENGINE_SPECS.items()):
            engines[name] = {
                "models": list(models_map.get(name, [])),
                "default_model": spec.default_model,
                "supports_resume": supports_capability(name, "resume"),
            }
        state = self._runtime.state
        return {
            "version": _PKG_VERSION,
            "engines": engines,
            "permissions": sorted(PRESETS),
            "schemas": list(SCHEMA_NAMES),
            "queue": {
                "exec_jsonl": str(state._exec_jsonl_path),
                "state_dir": str(state._state_dir),
            },
        }
