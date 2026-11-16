"""ghdag mcp — stdio MCP server for queue operations."""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

from ghdag.config.env import resolve_runtime_paths
from ghdag.config.env import state_dir as resolve_state_dir
from ghdag.core.exceptions import ConfigError
from ghdag.mcp.server import McpServer
from ghdag.mcp.tools import McpRuntime, McpToolRegistry
from ghdag.pipeline.order import InlineOrderBuilder
from ghdag.pipeline.task_queue import TaskQueue


def _normalize_read_paths(paths: list[str] | None) -> frozenset[str]:
    if not paths:
        return frozenset()
    normalized: set[str] = set()
    for raw in paths:
        if Path(raw).is_absolute() or raw.startswith("/"):
            raise ConfigError(
                "Invalid MCP startup arguments",
                code="MCP_ARGUMENT_INVALID",
                hint="path must be relative to --repo-root",
            )
        if ".." in PurePosixPath(raw).parts:
            raise ConfigError(
                "Invalid MCP startup arguments",
                code="MCP_ARGUMENT_INVALID",
                hint="path must not contain '..'",
            )
        normalized.add(str(PurePosixPath(raw)))
    return frozenset(normalized)


def cmd_mcp(args) -> None:
    """Run the MCP JSON-RPC server on stdin/stdout."""
    repo_root = Path(args.repo_root).resolve()
    exec_jsonl = Path(args.exec_jsonl)
    if not exec_jsonl.is_absolute():
        exec_jsonl = (repo_root / exec_jsonl).resolve()
    queue_dir = str(exec_jsonl.parent)

    if args.state_dir is not None:
        state_dir = Path(args.state_dir).resolve()
    elif exec_jsonl.parent.name == "jobs":
        state_dir = resolve_state_dir(exec_jsonl.parent.parent) / ".pipeline-state"
    else:
        state_dir = resolve_state_dir(repo_root) / ".pipeline-state"

    read_paths = _normalize_read_paths(getattr(args, "read_path", None))

    paths = resolve_runtime_paths(exec_jsonl)
    task_queue = TaskQueue(
        state_dir=str(state_dir),
        exec_jsonl_path=str(exec_jsonl),
        order_builder=InlineOrderBuilder(),
        queue_dir=queue_dir,
    )
    runtime = McpRuntime(
        task_queue=task_queue,
        queue_dir=queue_dir,
        done_dir=paths.done_dir,
        running_dir=paths.running_dir,
        repo_root=repo_root,
        read_paths=read_paths,
    )
    server = McpServer(McpToolRegistry(runtime))
    server.serve(sys.stdin, sys.stdout)
