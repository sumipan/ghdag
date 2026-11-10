"""ghdag mcp — stdio MCP server for queue operations."""

from __future__ import annotations

import sys
from pathlib import Path

from ghdag.config.env import state_dir as resolve_state_dir
from ghdag.mcp.server import McpServer
from ghdag.mcp.tools import McpRuntime, McpToolRegistry
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.pipeline.order import InlineOrderBuilder
from ghdag.pipeline.state import PipelineState


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

    pipeline_state = PipelineState(
        state_dir=str(state_dir),
        exec_jsonl_path=str(exec_jsonl),
    )
    pipeline = LLMPipelineAPI(
        pipeline_state=pipeline_state,
        order_builder=InlineOrderBuilder(),
        queue_dir=queue_dir,
    )
    runtime = McpRuntime(
        state=pipeline_state,
        pipeline=pipeline,
        queue_dir=queue_dir,
        done_dir=exec_jsonl.parent / "done",
        running_dir=exec_jsonl.parent / "running",
    )
    server = McpServer(McpToolRegistry(runtime))
    server.serve(sys.stdin, sys.stdout)
