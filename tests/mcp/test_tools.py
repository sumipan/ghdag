"""MCP tool implementations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghdag.core.capabilities import PRESETS
from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.core.vocabulary import DONE_ENGINE_ERROR
from ghdag.io.done import mark_done
from ghdag.mcp.server import McpServer
from ghdag.mcp.tools import TOOL_NAMES, McpRuntime, McpToolRegistry
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.pipeline.order import InlineOrderBuilder
from ghdag.pipeline.state import PipelineState


def _runtime(tmp_path: Path) -> McpRuntime:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    exec_jsonl = jobs / "exec.jsonl"
    exec_jsonl.touch()
    state_dir = tmp_path / ".pipeline-state"
    state_dir.mkdir()
    (jobs / "done").mkdir()
    (jobs / "running").mkdir()
    ps = PipelineState(state_dir=state_dir, exec_jsonl_path=exec_jsonl)
    pipeline = LLMPipelineAPI(
        pipeline_state=ps,
        order_builder=InlineOrderBuilder(),
        queue_dir=str(jobs),
    )
    return McpRuntime(
        state=ps,
        pipeline=pipeline,
        queue_dir=str(jobs),
        done_dir=jobs / "done",
        running_dir=jobs / "running",
    )


def _server(runtime: McpRuntime) -> McpServer:
    return McpServer(McpToolRegistry(runtime))


def _call(server: McpServer, name: str, arguments: dict) -> dict:
    resp = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert resp is not None
    return resp["result"]


class TestToolCatalog:
    def test_tool_name_set(self, tmp_path):
        runtime = _runtime(tmp_path)
        names = {t["name"] for t in McpToolRegistry(runtime).list_tools()}
        assert names == set(TOOL_NAMES)
        assert names == {"ghdag_enqueue", "ghdag_status"}

    def test_input_schemas_are_closed_objects(self, tmp_path):
        runtime = _runtime(tmp_path)
        for tool in McpToolRegistry(runtime).list_tools():
            schema = tool["inputSchema"]
            assert schema["type"] == "object"
            assert schema["additionalProperties"] is False

    def test_enqueue_schema_contract(self, tmp_path):
        runtime = _runtime(tmp_path)
        registry = McpToolRegistry(runtime)
        enqueue = next(t for t in registry.list_tools() if t["name"] == "ghdag_enqueue")
        step_items = enqueue["inputSchema"]["properties"]["steps"]["items"]
        assert step_items["required"] == ["engine"]
        assert step_items["properties"]["engine"]["enum"] == sorted(ENGINE_SPECS)
        assert step_items["properties"]["permission"]["enum"] == sorted(PRESETS)
        assert (
            step_items["properties"]["model"]["description"]
            == "Omit for the engine default; ghdag llm --list-models lists the allowed models."
        )


class TestEnqueue:
    def test_single_step_without_id(self, tmp_path):
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        result = _call(
            server,
            "ghdag_enqueue",
            {"steps": [{"engine": "shell", "command": "echo hi"}]},
        )
        assert result["isError"] is False
        uuids = result["structuredContent"]["uuids"]
        assert "step-1" in uuids
        lines = [
            ln
            for ln in runtime.state._exec_jsonl_path.read_text().splitlines()
            if ln.strip()
        ]
        assert len(lines) == 1
        line = json.loads(lines[0])
        assert line["uuid"] == uuids["step-1"]

    def test_llm_safe_default(self, tmp_path):
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        result = _call(
            server,
            "ghdag_enqueue",
            {
                "steps": [
                    {
                        "engine": "claude",
                        "prompt": "say hi",
                        "model": "claude-sonnet-4-6",
                    }
                ],
            },
        )
        assert result["isError"] is False
        line = json.loads(runtime.state._exec_jsonl_path.read_text().strip())
        cmd = line["command"]
        assert "--dangerously-skip-permissions" not in " ".join(cmd)
        assert line["annotations"]["safe_default_preset"] == "text_only"

    def test_sibling_dependency_mapping(self, tmp_path):
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        result = _call(
            server,
            "ghdag_enqueue",
            {
                "steps": [
                    {"id": "a", "engine": "shell", "command": "echo a"},
                    {
                        "id": "b",
                        "engine": "shell",
                        "command": "echo b",
                        "depends": ["a"],
                    },
                ]
            },
        )
        assert result["isError"] is False
        uuids = result["structuredContent"]["uuids"]
        lines = [
            json.loads(ln)
            for ln in runtime.state._exec_jsonl_path.read_text().splitlines()
            if ln.strip()
        ]
        b_line = next(row for row in lines if row["uuid"] == uuids["b"])
        assert b_line["depends"] == [uuids["a"]]

    def test_unknown_sibling_is_atomic(self, tmp_path):
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        result = _call(
            server,
            "ghdag_enqueue",
            {
                "steps": [
                    {
                        "id": "b",
                        "engine": "shell",
                        "command": "echo b",
                        "depends": ["zzz"],
                    },
                ]
            },
        )
        assert result["isError"] is True
        assert result["structuredContent"]["code"] == "STEP_DEPENDENCY_UNKNOWN"
        assert runtime.state._exec_jsonl_path.read_text() == ""

    def test_unknown_engine(self, tmp_path):
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        result = _call(
            server,
            "ghdag_enqueue",
            {"steps": [{"engine": "nope", "prompt": "x"}]},
        )
        assert result["isError"] is True
        assert result["structuredContent"]["code"] == "ENGINE_UNKNOWN"

    @pytest.mark.parametrize(
        "step",
        [
            {"id": "a", "engine": "shell", "command": "echo a", "prompt": 1},
            {"id": "a", "engine": "claude", "prompt": "x", "command": []},
        ],
    )
    def test_rejects_non_string_bodies(self, tmp_path, step):
        runtime = _runtime(tmp_path)
        result = _call(
            _server(runtime),
            "ghdag_enqueue",
            {"steps": [step]},
        )
        assert result["isError"] is True
        assert result["structuredContent"]["code"] == "MCP_ARGUMENT_INVALID"
        assert runtime.state._exec_jsonl_path.read_text() == ""

    def test_rejects_duplicate_step_ids(self, tmp_path):
        runtime = _runtime(tmp_path)
        result = _call(
            _server(runtime),
            "ghdag_enqueue",
            {
                "steps": [
                    {"id": "a", "engine": "shell", "command": "echo first"},
                    {"id": "a", "engine": "shell", "command": "echo second"},
                ]
            },
        )
        assert result["isError"] is True
        assert result["structuredContent"]["code"] == "MCP_ARGUMENT_INVALID"
        assert runtime.state._exec_jsonl_path.read_text() == ""


class TestStatus:
    def test_pending_status(self, tmp_path):
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        enq = _call(
            server,
            "ghdag_enqueue",
            {"steps": [{"engine": "shell", "command": "echo x"}]},
        )
        uid = enq["structuredContent"]["uuids"]["step-1"]
        st = _call(server, "ghdag_status", {"uuids": [uid]})
        assert st["structuredContent"]["tasks"][0]["status"] == "pending"

    def test_pending_then_engine_error(self, tmp_path):
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        enq = _call(
            server,
            "ghdag_enqueue",
            {"steps": [{"engine": "shell", "command": "echo x"}]},
        )
        uid = enq["structuredContent"]["uuids"]["step-1"]
        mark_done(runtime.done_dir, uid, DONE_ENGINE_ERROR)
        st = _call(server, "ghdag_status", {"uuids": [uid]})
        task = st["structuredContent"]["tasks"][0]
        assert task["status"] == "failed"
        assert task["failure_class"] == "ENGINE_ERROR"
        assert task["cause"] == "transient"

    def test_uuids_and_issue_are_exclusive(self, tmp_path):
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        result = _call(
            server,
            "ghdag_status",
            {"uuids": ["x"], "issue": 1, "handler": "h", "workflow": "w"},
        )
        assert result["isError"] is True
        assert result["structuredContent"]["code"] == "MCP_ARGUMENT_INVALID"
