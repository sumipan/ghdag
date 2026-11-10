"""MCP server JSON-RPC behavior (#6297)."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock

from ghdag.core.exceptions import ConfigError
from ghdag.mcp.server import SUPPORTED_PROTOCOL_VERSION, McpServer


def _tools_listing() -> MagicMock:
    tools = MagicMock()
    tools.list_tools.return_value = [
        {
            "name": "ghdag_describe",
            "description": "d",
            "inputSchema": {"type": "object", "additionalProperties": False},
        }
    ]
    tools.call_tool.return_value = {"version": "0.0.0"}
    return tools


class TestInitialize:
    def test_supported_protocol_version_echoed(self):
        server = McpServer(_tools_listing())
        resp = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": SUPPORTED_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            }
        )
        assert resp is not None
        assert resp["result"]["protocolVersion"] == SUPPORTED_PROTOCOL_VERSION
        assert resp["result"]["capabilities"] == {"tools": {}}
        assert resp["result"]["serverInfo"]["name"] == "ghdag"

    def test_unknown_protocol_counter_offered(self):
        server = McpServer(_tools_listing())
        resp = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2099-01-01"},
            }
        )
        assert resp is not None
        assert resp["result"]["protocolVersion"] == SUPPORTED_PROTOCOL_VERSION


class TestNotificationsAndErrors:
    def test_initialized_notification_returns_none(self):
        server = McpServer(_tools_listing())
        assert (
            server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
            is None
        )

    def test_unknown_method(self):
        server = McpServer(_tools_listing())
        resp = server.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "nope"},
        )
        assert resp is not None
        assert resp["error"]["code"] == -32601

    def test_parse_error_on_serve(self):
        server = McpServer(_tools_listing())
        stdin = io.StringIO("not json\n")
        stdout = io.StringIO()
        server.serve(stdin, stdout)
        line = json.loads(stdout.getvalue().strip())
        assert line["error"]["code"] == -32700


class TestToolsCall:
    def test_ghdag_error_surfaces_as_is_error(self):
        tools = MagicMock()
        tools.list_tools.return_value = []
        tools.call_tool.side_effect = ConfigError(
            "bad",
            code="DEPENDENCY_UUID_UNKNOWN",
            hint="enqueue the dependency first or drop it",
        )
        server = McpServer(tools)
        resp = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "ghdag_enqueue_task", "arguments": {}},
            }
        )
        assert resp is not None
        result = resp["result"]
        assert result["isError"] is True
        assert result["structuredContent"]["code"] == "DEPENDENCY_UUID_UNKNOWN"
        assert result["content"][0]["text"].startswith(
            "error[DEPENDENCY_UUID_UNKNOWN]:"
        )

    def test_tools_list_names(self):
        tools = _tools_listing()
        server = McpServer(tools)
        resp = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
        assert resp is not None
        names = {t["name"] for t in resp["result"]["tools"]}
        assert names == {"ghdag_describe"}
