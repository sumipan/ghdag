"""MCP tool implementations (#6297)."""

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
from ghdag.schemas import SCHEMA_NAMES


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

    def test_input_schemas_are_closed_objects(self, tmp_path):
        runtime = _runtime(tmp_path)
        for tool in McpToolRegistry(runtime).list_tools():
            schema = tool["inputSchema"]
            assert schema["type"] == "object"
            assert schema["additionalProperties"] is False


class TestEnqueueTask:
    def test_shell_enqueue_writes_exec_jsonl(self, tmp_path):
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        result = _call(
            server,
            "ghdag_enqueue_task",
            {"engine": "shell", "command": "echo hi"},
        )
        assert result["isError"] is False
        line = json.loads(runtime.state._exec_jsonl_path.read_text().strip())
        assert line["uuid"] == result["structuredContent"]["uuid"]
        assert line["engine"] == "shell"

    def test_claude_text_only_has_no_danger_flag(self, tmp_path):
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        result = _call(
            server,
            "ghdag_enqueue_task",
            {
                "engine": "claude",
                "prompt": "say hi",
                "model": "claude-sonnet-4-6",
            },
        )
        assert result["isError"] is False
        line = json.loads(runtime.state._exec_jsonl_path.read_text().strip())
        cmd = line["command"]
        assert "--dangerously-skip-permissions" not in " ".join(cmd)
        assert line["annotations"]["safe_default_preset"] == "text_only"

    def test_unknown_dependency_uuid(self, tmp_path):
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        result = _call(
            server,
            "ghdag_enqueue_task",
            {
                "engine": "claude",
                "prompt": "x",
                "depends": ["00000000-0000-0000-0000-000000000000"],
            },
        )
        assert result["isError"] is True
        assert result["structuredContent"]["code"] == "DEPENDENCY_UUID_UNKNOWN"
        assert result["content"][0]["text"].startswith(
            "error[DEPENDENCY_UUID_UNKNOWN]:"
        )

    def test_unknown_engine(self, tmp_path):
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        result = _call(
            server,
            "ghdag_enqueue_task",
            {"engine": "nope", "prompt": "x"},
        )
        assert result["isError"] is True
        assert result["structuredContent"]["code"] == "ENGINE_UNKNOWN"

    @pytest.mark.parametrize(
        "arguments",
        [
            {"engine": "shell", "prompt": "x", "command": "echo x"},
            {"engine": "claude"},
        ],
    )
    def test_invalid_argument_shapes(self, tmp_path, arguments):
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        result = _call(server, "ghdag_enqueue_task", arguments)
        assert result["isError"] is True
        assert result["structuredContent"]["code"] == "MCP_ARGUMENT_INVALID"


class TestEnqueueDag:
    def test_two_step_depends(self, tmp_path):
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        result = _call(
            server,
            "ghdag_enqueue_dag",
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
        assert "a" in uuids and "b" in uuids
        lines = [
            json.loads(ln)
            for ln in runtime.state._exec_jsonl_path.read_text().splitlines()
            if ln.strip()
        ]
        b_line = next(row for row in lines if row["uuid"] == uuids["b"])
        assert b_line["depends"] == [uuids["a"]]

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
            "ghdag_enqueue_dag",
            {"steps": [step]},
        )
        assert result["isError"] is True
        assert result["structuredContent"]["code"] == "MCP_ARGUMENT_INVALID"
        assert runtime.state._exec_jsonl_path.read_text() == ""

    def test_rejects_duplicate_step_ids(self, tmp_path):
        runtime = _runtime(tmp_path)
        result = _call(
            _server(runtime),
            "ghdag_enqueue_dag",
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


class TestGetStatus:
    def test_pending_then_engine_error(self, tmp_path):
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        enq = _call(
            server,
            "ghdag_enqueue_task",
            {"engine": "shell", "command": "echo x"},
        )
        uid = enq["structuredContent"]["uuid"]
        st = _call(server, "ghdag_get_status", {"uuids": [uid]})
        assert st["structuredContent"]["tasks"][0]["status"] == "pending"
        mark_done(runtime.done_dir, uid, DONE_ENGINE_ERROR)
        st2 = _call(server, "ghdag_get_status", {"uuids": [uid]})
        task = st2["structuredContent"]["tasks"][0]
        assert task["status"] == "failed"
        assert task["failure_class"] == "ENGINE_ERROR"
        assert task["cause"] == "transient"

    def test_uuids_and_issue_are_exclusive(self, tmp_path):
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        result = _call(
            server,
            "ghdag_get_status",
            {"uuids": ["x"], "issue": 1, "handler": "h", "workflow": "w"},
        )
        assert result["isError"] is True
        assert result["structuredContent"]["code"] == "MCP_ARGUMENT_INVALID"


class TestDescribe:
    def test_describe_payload(self, tmp_path):
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        result = _call(server, "ghdag_describe", {})
        body = result["structuredContent"]
        assert set(body["engines"]) == set(ENGINE_SPECS)
        assert "claude-sonnet-4-6" in body["engines"]["claude"]["models"]
        assert body["permissions"] == sorted(PRESETS)
        assert body["schemas"] == list(SCHEMA_NAMES)
