"""MCP tool implementations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghdag.core.capabilities import PERMISSIONS
from ghdag.core.engine_spec import ENGINE_SPECS
from ghdag.core.vocabulary import (
    DONE_CANCELLED,
    DONE_DEP_FAILED,
    DONE_EMPTY_RESULT,
    DONE_ENGINE_ENV_ERROR,
    DONE_ENGINE_ERROR,
    DONE_ENGINE_ERROR_FINAL,
    DONE_FANOUT_CHILD_FAILED,
    DONE_FANOUT_PARSE_FAILED,
    DONE_INTERACTIVE_PROMPT,
    DONE_ORPHAN_ARCHIVED,
    DONE_ORPHANED_ON_RESTART,
    DONE_PIPELINE_FAILED_PREFIX,
    DONE_REJECTED,
    DONE_REJECTED_FINAL,
    DONE_SKIPPED_MISSING_INPUT,
    DONE_SUCCESS,
    DONE_TIMEOUT,
    DONE_UNKNOWN_FAILURE,
    LEGACY_DONE_DEFERRED,
)
from ghdag.io.done import mark_done
from ghdag.mcp.server import McpServer
from ghdag.mcp.tools import MAX_READ_BYTES, TOOL_NAMES, McpRuntime, McpToolRegistry
from ghdag.pipeline.llm_pipeline import LLMPipelineAPI
from ghdag.pipeline.order import InlineOrderBuilder
from ghdag.pipeline.state import PipelineState


def _runtime(
    tmp_path: Path,
    *,
    read_paths: frozenset[str] | None = None,
) -> McpRuntime:
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
        repo_root=tmp_path,
        read_paths=read_paths or frozenset(),
    )


def _seed_uuid_record(
    runtime: McpRuntime,
    uid: str,
    *,
    result_path: str | None = None,
    done_marker: str | None = None,
) -> None:
    record: dict = {"uuid": uid, "command": "echo", "depends": []}
    if result_path is not None:
        record["result_path"] = result_path
    exec_path = Path(runtime.state._exec_jsonl_path)
    exec_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    if done_marker is not None:
        mark_done(runtime.done_dir, uid, done_marker)


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
        tools = McpToolRegistry(runtime).list_tools()
        names = {t["name"] for t in tools}
        assert names == set(TOOL_NAMES)
        assert names == {"ghdag_enqueue", "ghdag_status", "ghdag_read"}
        for tool in tools:
            assert tool["inputSchema"]["type"] == "object"
            assert tool["inputSchema"]["additionalProperties"] is False

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
        assert step_items["properties"]["permission"]["enum"] == sorted(PERMISSIONS)
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
            {"id": "a", "engine": 1, "command": "echo a"},
            {"id": "a", "engine": "shell", "command": "echo a", "permission": []},
        ],
    )
    def test_rejects_non_string_fields(self, tmp_path, step):
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

    @pytest.mark.parametrize(
        ("marker", "expected"),
        [
            (DONE_SUCCESS, "success"),
            ('{"status": "ok"}', "success"),
            (DONE_REJECTED, "failed"),
            (DONE_REJECTED_FINAL, "failed"),
            (DONE_ENGINE_ERROR, "failed"),
            (DONE_ENGINE_ERROR_FINAL, "failed"),
            (DONE_ENGINE_ENV_ERROR, "failed"),
            (DONE_INTERACTIVE_PROMPT, "failed"),
            (DONE_TIMEOUT, "failed"),
            (DONE_CANCELLED, "failed"),
            (DONE_DEP_FAILED, "failed"),
            (DONE_EMPTY_RESULT, "failed"),
            (f"{DONE_PIPELINE_FAILED_PREFIX}x", "failed"),
            (DONE_FANOUT_CHILD_FAILED, "failed"),
            (DONE_FANOUT_PARSE_FAILED, "failed"),
            (DONE_SKIPPED_MISSING_INPUT, "failed"),
            (DONE_UNKNOWN_FAILURE, "failed"),
            (DONE_ORPHAN_ARCHIVED, "failed"),
            (DONE_ORPHANED_ON_RESTART, "failed"),
            (LEGACY_DONE_DEFERRED, "failed"),
            ("1", "failed"),
        ],
    )
    def test_uuid_status_per_done_marker(self, tmp_path, marker, expected):
        """Refs sumipan/nexus#6807 — uuid ghdag_status maps done markers to four values."""
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        enq = _call(
            server,
            "ghdag_enqueue",
            {"steps": [{"engine": "shell", "command": "echo x"}]},
        )
        uid = enq["structuredContent"]["uuids"]["step-1"]
        mark_done(runtime.done_dir, uid, marker)
        st = _call(server, "ghdag_status", {"uuids": [uid]})
        assert st["structuredContent"]["tasks"][0]["status"] == expected

    @pytest.mark.parametrize(
        "dep_setup",
        [
            "no_done",
            "success",
            "engine_error",
            "running",
        ],
    )
    def test_uuid_status_pending_regardless_of_depends(
        self, tmp_path, dep_setup: str
    ):
        """Refs sumipan/nexus#6807 — undoned uuid stays pending regardless of deps."""
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        enq = _call(
            server,
            "ghdag_enqueue",
            {
                "steps": [
                    {"id": "step-1", "engine": "shell", "command": "echo a"},
                    {
                        "id": "step-2",
                        "engine": "shell",
                        "command": "echo b",
                        "depends": ["step-1"],
                    },
                ]
            },
        )
        uuids = enq["structuredContent"]["uuids"]
        dep_uid = uuids["step-1"]
        target_uid = uuids["step-2"]
        if dep_setup == "success":
            mark_done(runtime.done_dir, dep_uid, DONE_SUCCESS)
        elif dep_setup == "engine_error":
            mark_done(runtime.done_dir, dep_uid, DONE_ENGINE_ERROR)
        elif dep_setup == "running":
            (runtime.running_dir / f"{dep_uid}.json").write_text("{}", encoding="utf-8")
        st = _call(server, "ghdag_status", {"uuids": [target_uid]})
        assert st["structuredContent"]["tasks"][0]["status"] == "pending"

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


class TestStatusIncludeResult:
    def test_status_include_result_returns_body(self, tmp_path):
        """Refs sumipan/nexus#6837 — finished task returns result file body."""
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        uid = "00000000-0000-0000-0000-0000000000aa"
        rel = "jobs/results/out.md"
        body = "hello result"
        (tmp_path / "jobs" / "results").mkdir(parents=True)
        (tmp_path / rel).write_text(body, encoding="utf-8")
        _seed_uuid_record(
            runtime,
            uid,
            result_path=rel,
            done_marker=DONE_SUCCESS,
        )
        st = _call(
            server,
            "ghdag_status",
            {"uuids": [uid], "include_result": True},
        )
        assert st["isError"] is False
        task = st["structuredContent"]["tasks"][0]
        assert task["result"] == body
        assert task["result_truncated"] is False
        assert task["result_reason"] is None

    def test_status_include_result_truncates(self, tmp_path):
        """Refs sumipan/nexus#6837 — oversized result files are capped."""
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        uid = "00000000-0000-0000-0000-0000000000ab"
        rel = "jobs/results/big.md"
        (tmp_path / "jobs" / "results").mkdir(parents=True)
        payload = "x" * (MAX_READ_BYTES + 100)
        (tmp_path / rel).write_bytes(payload.encode("utf-8"))
        _seed_uuid_record(
            runtime,
            uid,
            result_path=rel,
            done_marker=DONE_SUCCESS,
        )
        st = _call(
            server,
            "ghdag_status",
            {"uuids": [uid], "include_result": True},
        )
        task = st["structuredContent"]["tasks"][0]
        assert len(task["result"]) == MAX_READ_BYTES
        assert task["result_truncated"] is True
        assert task["result_reason"] is None

    def test_status_include_result_missing_file(self, tmp_path):
        """Refs sumipan/nexus#6837 — missing result_path target is soft failure."""
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        uid = "00000000-0000-0000-0000-0000000000ac"
        _seed_uuid_record(
            runtime,
            uid,
            result_path="jobs/results/missing.md",
            done_marker=DONE_SUCCESS,
        )
        st = _call(
            server,
            "ghdag_status",
            {"uuids": [uid], "include_result": True},
        )
        assert st["isError"] is False
        task = st["structuredContent"]["tasks"][0]
        assert task["result"] is None
        assert task["result_reason"] == "not_found"

    def test_status_without_include_result_keys_unchanged(self, tmp_path):
        """Refs sumipan/nexus#6837 — default status shape stays seven keys."""
        runtime = _runtime(tmp_path)
        server = _server(runtime)
        uid = "00000000-0000-0000-0000-0000000000ad"
        _seed_uuid_record(runtime, uid, done_marker=DONE_SUCCESS)
        st = _call(server, "ghdag_status", {"uuids": [uid]})
        task = st["structuredContent"]["tasks"][0]
        assert set(task) == {
            "uuid",
            "status",
            "done_value",
            "failure_class",
            "cause",
            "retry_policy",
            "result_path",
        }

    def test_status_include_result_with_issue_rejected(self, tmp_path):
        """Refs sumipan/nexus#6837 — include_result is uuid-only."""
        runtime = _runtime(tmp_path)
        result = _call(
            _server(runtime),
            "ghdag_status",
            {
                "issue": 1,
                "handler": "h",
                "workflow": "w",
                "include_result": True,
            },
        )
        assert result["isError"] is True
        assert result["structuredContent"]["code"] == "MCP_ARGUMENT_INVALID"


class TestGhdagRead:
    def test_read_listed_path_returns_body(self, tmp_path):
        """Refs sumipan/nexus#6837 — ghdag_read returns listed file content."""
        rel = "state/x.json"
        body = '{"ok": true}'
        (tmp_path / "state").mkdir()
        (tmp_path / rel).write_text(body, encoding="utf-8")
        runtime = _runtime(tmp_path, read_paths=frozenset({rel}))
        result = _call(_server(runtime), "ghdag_read", {"path": rel})
        assert result["isError"] is False
        payload = result["structuredContent"]
        assert payload["content"] == body
        assert payload["truncated"] is False
        assert payload["reason"] is None

    @pytest.mark.parametrize(
        ("arguments", "secret_rel"),
        [
            ({"path": "state/other.json"}, "state/listed.json"),
            ({"path": "../secrets.txt"}, "state/listed.json"),
            ({"path": "/etc/passwd"}, "state/listed.json"),
        ],
    )
    def test_read_rejects_unlisted_dotdot_absolute(
        self, tmp_path, arguments, secret_rel
    ):
        """Refs sumipan/nexus#6837 — invalid paths never return file bodies."""
        (tmp_path / "state").mkdir()
        secret = "TOP SECRET"
        (tmp_path / secret_rel).write_text(secret, encoding="utf-8")
        runtime = _runtime(tmp_path, read_paths=frozenset({secret_rel}))
        result = _call(_server(runtime), "ghdag_read", arguments)
        assert result["isError"] is True
        assert result["structuredContent"]["code"] == "MCP_ARGUMENT_INVALID"
        assert result["structuredContent"]["hint"]
        assert secret not in json.dumps(result)

    def test_read_without_read_paths_rejects_all(self, tmp_path):
        """Refs sumipan/nexus#6837 — empty allowlist rejects every path."""
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "x.json").write_text("{}", encoding="utf-8")
        runtime = _runtime(tmp_path)
        result = _call(_server(runtime), "ghdag_read", {"path": "state/x.json"})
        assert result["isError"] is True
        assert result["structuredContent"]["code"] == "MCP_ARGUMENT_INVALID"

    def test_read_listed_missing_file_returns_null(self, tmp_path):
        """Refs sumipan/nexus#6837 — listed but missing file is not an error."""
        rel = "state/y.json"
        runtime = _runtime(tmp_path, read_paths=frozenset({rel}))
        result = _call(_server(runtime), "ghdag_read", {"path": rel})
        assert result["isError"] is False
        payload = result["structuredContent"]
        assert payload["content"] is None
        assert payload["reason"] == "not_found"
