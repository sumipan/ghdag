"""MCP tool definitions and implementations."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ghdag.core.capabilities import PERMISSIONS
from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.core.exceptions import ConfigError
from ghdag.core.models.workflow import StepConfig
from ghdag.io import exec_jsonl
from ghdag.io.audit import AuditContext
from ghdag.io.done import done_failure_class, interpret_done, read_done_content
from ghdag.pipeline.order import InlineOrderBuilder
from ghdag.pipeline.task_queue import TaskQueue
from ghdag.status import issue_status, running_tasks

MAX_READ_BYTES = 65536

TOOL_NAMES: tuple[str, ...] = (
    "ghdag_enqueue",
    "ghdag_status",
    "ghdag_read",
)

_ENQUEUE_KEYS = frozenset({"steps", "correlation_id"})
_DAG_STEP_KEYS = frozenset(
    {"id", "engine", "prompt", "command", "model", "depends", "permission"}
)
_STATUS_ISSUE_KEYS = frozenset({"issue", "handler", "workflow"})

_MODEL_DESCRIPTION = (
    "Omit for the engine default; ghdag llm --list-models lists the allowed models."
)


def _read_capped(path: Path) -> tuple[str | None, bool, str | None]:
    """Read at most MAX_READ_BYTES from path; return (text, truncated, reason)."""
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_READ_BYTES + 1)
    except FileNotFoundError:
        return None, False, "not_found"
    except IsADirectoryError:
        return None, False, "not_found"
    except OSError:
        return None, False, "unreadable"
    truncated = len(data) > MAX_READ_BYTES
    if truncated:
        data = data[:MAX_READ_BYTES]
    return data.decode("utf-8", errors="replace"), truncated, None


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


def _enqueue_schema() -> dict[str, Any]:
    engine_enum = sorted(ENGINE_SPECS)
    perm_enum = sorted(PERMISSIONS)
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
            "model": {"type": "string", "description": _MODEL_DESCRIPTION},
            "depends": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Sibling step ids this step depends on",
            },
            "permission": {
                "type": "string",
                "enum": perm_enum,
                "description": "Permission preset",
            },
        },
        required=["engine"],
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
                    {"engine": "shell", "command": "echo a"},
                    {
                        "id": "b",
                        "engine": "shell",
                        "command": "echo b",
                        "depends": ["step-1"],
                    },
                ]
            }
        ],
    )


def _status_schema() -> dict[str, Any]:
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
        "include_result": {
            "type": "boolean",
            "description": "When true with uuids, embed capped result file bodies",
        },
        **issue_props,
    }
    return _schema_object(
        props,
        examples=[{"uuids": ["00000000-0000-0000-0000-000000000001"]}],
    )


def _read_schema() -> dict[str, Any]:
    return _schema_object(
        {"path": {"type": "string", "description": "Relative path under --repo-root"}},
        required=["path"],
    )


def _mcp_task_status(uuid: str, done_dir: Path, *, running: set[str]) -> str:
    """Task status for uuid-scoped ghdag_status: success | failed | pending | running."""
    if uuid in running:
        return "running"
    raw = read_done_content(done_dir, uuid)
    if raw is None:
        return "pending"
    if interpret_done(raw) == "success":
        return "success"
    return "failed"


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
        "name": "ghdag_enqueue",
        "description": "Enqueue one or more dependent steps through TaskQueue.enqueue",
        "inputSchema": _enqueue_schema(),
    },
    {
        "name": "ghdag_status",
        "description": "Read task status by uuid or Issue handler workflow",
        "inputSchema": _status_schema(),
    },
    {
        "name": "ghdag_read",
        "description": "Read a repo file listed by ghdag mcp --read-path",
        "inputSchema": _read_schema(),
    },
]


@dataclass
class McpRuntime:
    """Paths and task queue shared by MCP tools."""

    task_queue: TaskQueue
    queue_dir: str
    done_dir: Path
    running_dir: Path
    repo_root: Path
    read_paths: frozenset[str] = frozenset()


class McpToolRegistry:
    """Registry of MCP tools backed by pipeline state."""

    def __init__(self, runtime: McpRuntime) -> None:
        self._runtime = runtime

    def list_tools(self) -> list[dict[str, Any]]:
        return [dict(spec) for spec in _TOOL_SPECS]

    def call_tool(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        if arguments is None:
            arguments = {}
        if name == "ghdag_enqueue":
            return self._enqueue(arguments)
        if name == "ghdag_status":
            return self._status(arguments)
        if name == "ghdag_read":
            return self._read(arguments)
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

    def _resolve_result_path(self, result_path: str) -> Path:
        path = Path(result_path)
        if not path.is_absolute():
            path = self._runtime.repo_root / path
        return path

    def _include_result_fields(
        self,
        *,
        status: str,
        result_path: str | None,
    ) -> dict[str, Any]:
        if status in ("pending", "running"):
            return {
                "result": None,
                "result_truncated": False,
                "result_reason": "not_finished",
            }
        if not result_path:
            return {
                "result": None,
                "result_truncated": False,
                "result_reason": "no_result_path",
            }
        content, truncated, reason = _read_capped(self._resolve_result_path(result_path))
        return {
            "result": content,
            "result_truncated": truncated,
            "result_reason": reason,
        }

    def _normalize_relative_path(self, raw: str) -> str:
        return str(PurePosixPath(raw))

    def _validate_read_path_arg(self, raw: Any) -> str:
        if not isinstance(raw, str) or not raw:
            raise self._mcp_invalid("path must be a non-empty string")
        if Path(raw).is_absolute() or raw.startswith("/"):
            raise self._mcp_invalid("path must be relative to --repo-root")
        if ".." in PurePosixPath(raw).parts:
            raise self._mcp_invalid("path must not contain '..'")
        return self._normalize_relative_path(raw)

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

    def _enqueue(self, args: dict[str, Any]) -> dict[str, Any]:
        self._validate_keys(args, _ENQUEUE_KEYS)
        steps_raw = args.get("steps")
        if not isinstance(steps_raw, list):
            raise self._mcp_invalid("steps must be an array")
        if not (1 <= len(steps_raw) <= 20):
            raise self._mcp_invalid("steps must contain between 1 and 20 items")

        step_configs: list[StepConfig] = []
        step_ids: set[str] = set()
        for index, raw in enumerate(steps_raw):
            if not isinstance(raw, dict):
                raise self._mcp_invalid("each step must be an object")
            self._validate_keys(raw, _DAG_STEP_KEYS)
            step_id = raw.get("id")
            if step_id is None:
                step_id = f"step-{index + 1}"
            elif not isinstance(step_id, str) or not step_id:
                raise self._mcp_invalid("id must be a non-empty string when provided")
            if step_id in step_ids:
                raise self._mcp_invalid(f"step ids must be unique: {step_id!r}")
            step_ids.add(step_id)
            engine = raw.get("engine")
            if not isinstance(engine, str):
                raise self._mcp_invalid("engine must be a string")
            if engine not in ENGINE_SPECS:
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
            if permission is not None and (
                not isinstance(permission, str) or permission not in PERMISSIONS
            ):
                raise self._mcp_invalid(f"permission must be one of {sorted(PERMISSIONS)}")
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

        lines = self._runtime.task_queue.enqueue(
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
        path = self._runtime.task_queue.exec_jsonl_path
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

    def _status(self, args: dict[str, Any]) -> dict[str, Any]:
        allowed = _STATUS_ISSUE_KEYS | frozenset({"uuids", "include_result"})
        self._validate_keys(args, allowed)
        has_uuids = "uuids" in args
        has_issue = any(k in args for k in _STATUS_ISSUE_KEYS)
        if has_uuids and has_issue:
            raise self._mcp_invalid("use either uuids or issue/handler/workflow, not both")
        if not has_uuids and not has_issue:
            raise self._mcp_invalid("provide uuids or issue, handler, and workflow")

        include_result = False
        if "include_result" in args:
            if has_issue:
                raise self._mcp_invalid("include_result is only supported with uuids")
            value = args["include_result"]
            if not isinstance(value, bool):
                raise self._mcp_invalid("include_result must be a boolean")
            include_result = value

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
                exec_jsonl_path=self._runtime.task_queue.exec_jsonl_path,
                state_dir=self._runtime.task_queue.state_dir,
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
            status = _mcp_task_status(
                uid,
                self._runtime.done_dir,
                running=running,
            )
            done_raw = read_done_content(self._runtime.done_dir, uid)
            done_value, failure_class, cause, retry_policy = _mcp_done_fields(
                done_raw,
                status=status,
            )
            entry: dict[str, Any] = {
                "uuid": uid,
                "status": status,
                "done_value": done_value,
                "failure_class": failure_class,
                "cause": cause,
                "retry_policy": retry_policy,
                "result_path": record.get("result_path") if record else None,
            }
            if include_result:
                rp = record.get("result_path") if record else None
                rp_str = rp if isinstance(rp, str) else None
                entry.update(
                    self._include_result_fields(status=status, result_path=rp_str)
                )
            tasks.append(entry)
        return {"tasks": tasks}

    def _read(self, args: dict[str, Any]) -> dict[str, Any]:
        self._validate_keys(args, frozenset({"path"}))
        normalized = self._validate_read_path_arg(args.get("path"))
        allowed = self._runtime.read_paths
        if normalized not in allowed:
            if allowed:
                raise self._mcp_invalid(
                    f"path is not listed by --read-path; allowed: {sorted(allowed)}"
                )
            raise self._mcp_invalid(
                "no paths are readable; start ghdag mcp with --read-path"
            )
        content, truncated, reason = _read_capped(self._runtime.repo_root / normalized)
        return {
            "path": normalized,
            "content": content,
            "truncated": truncated,
            "reason": reason,
        }
