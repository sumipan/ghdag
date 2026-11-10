"""MCP JSON-RPC 2.0 server over newline-delimited stdio."""

from __future__ import annotations

import json
import logging
from importlib.metadata import PackageNotFoundError, version
from typing import IO, Any, Protocol

from ghdag.core.exceptions import GhdagError

try:
    _PKG_VERSION = version("ghdag")
except PackageNotFoundError:
    _PKG_VERSION = "unknown"

logger = logging.getLogger(__name__)

JSONRPC_VERSION = "2.0"
SUPPORTED_PROTOCOL_VERSION = "2025-06-18"


class ToolRegistryLike(Protocol):
    """Minimal tool surface required by :class:`McpServer`."""

    def list_tools(self) -> list[dict[str, Any]]:
        ...

    def call_tool(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        ...


class McpServer:
    """One JSON-RPC request per line on stdin; responses on stdout only."""

    def __init__(
        self,
        tools: ToolRegistryLike,
        *,
        name: str = "ghdag",
        version: str = _PKG_VERSION,
    ) -> None:
        self._tools = tools
        self._name = name
        self._version = version

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle one JSON-RPC request dict. Returns None for notifications."""
        if not isinstance(message, dict):
            return self._error(None, -32600, "Invalid Request")

        jsonrpc = message.get("jsonrpc")
        if jsonrpc != JSONRPC_VERSION:
            return self._error(message.get("id"), -32600, "Invalid Request")

        method = message.get("method")
        if not isinstance(method, str) or not method:
            return self._error(message.get("id"), -32600, "Invalid Request")

        msg_id = message.get("id")
        is_notification = "id" not in message

        if method == "notifications/initialized":
            return None

        if is_notification:
            return None

        params = message.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return self._error(msg_id, -32600, "Invalid Request")

        try:
            if method == "initialize":
                return self._handle_initialize(msg_id, params)
            if method == "ping":
                return self._result(msg_id, {})
            if method == "tools/list":
                return self._handle_tools_list(msg_id)
            if method == "tools/call":
                return self._handle_tools_call(msg_id, params)
            return self._error(msg_id, -32601, f"Method not found: {method}")
        except Exception as exc:
            logger.exception("Unhandled error in MCP method %s", method)
            return self._error(msg_id, -32603, str(exc))

    def serve(self, stdin: IO[str], stdout: IO[str]) -> None:
        """Read newline-delimited JSON-RPC until EOF."""
        for raw_line in stdin:
            line = raw_line.strip()
            if not line:
                continue
            msg_id: Any = None
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                response = self._error(None, -32700, "Parse error")
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()
                continue

            if isinstance(message, dict):
                msg_id = message.get("id")

            try:
                response = self.handle(message) if isinstance(message, dict) else self._error(
                    msg_id, -32600, "Invalid Request",
                )
            except Exception as exc:
                logger.exception("MCP serve loop error")
                response = self._error(msg_id, -32603, str(exc))

            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()

    def _negotiate_protocol_version(self, requested: Any) -> str:
        if requested == SUPPORTED_PROTOCOL_VERSION:
            return str(requested)
        return SUPPORTED_PROTOCOL_VERSION

    def _handle_initialize(self, msg_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        protocol = self._negotiate_protocol_version(params.get("protocolVersion"))
        return self._result(
            msg_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": self._name, "version": self._version},
            },
        )

    def _handle_tools_list(self, msg_id: Any) -> dict[str, Any]:
        tools = self._tools.list_tools()
        return self._result(msg_id, {"tools": tools})

    def _handle_tools_call(self, msg_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return self._error(msg_id, -32602, "Invalid params: name is required")

        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return self._error(msg_id, -32602, "Invalid params: arguments must be an object")

        try:
            structured = self._tools.call_tool(name, arguments)
        except GhdagError as exc:
            text = f"error[{exc.code}]: {exc.message}"
            if exc.hint:
                text += f"\nhint: {exc.hint}"
            return self._result(
                msg_id,
                {
                    "content": [{"type": "text", "text": text}],
                    "structuredContent": exc.to_dict(),
                    "isError": True,
                },
            )
        except KeyError:
            return self._error(msg_id, -32602, f"Unknown tool: {name}")
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            return self._error(msg_id, -32603, str(exc))

        payload = json.dumps(structured, ensure_ascii=False)
        return self._result(
            msg_id,
            {
                "content": [{"type": "text", "text": payload}],
                "structuredContent": structured,
                "isError": False,
            },
        )

    @staticmethod
    def _result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": msg_id,
            "error": {"code": code, "message": message},
        }
