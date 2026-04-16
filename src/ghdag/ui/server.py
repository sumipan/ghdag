"""Lightweight HTTP server with SSE for the ghdag Web UI dashboard."""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path
from typing import Optional

from .monitor import (
    Row,
    build_rows,
    filter_rows,
    apply_default_monitor_filters,
    relayout_tree_for_visible_rows,
)

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


def _read_static(filename: str) -> bytes:
    return (_STATIC_DIR / filename).read_bytes()


def _build_snapshot(repo_root: Path, max_visible: int = 30) -> list[dict]:
    rows, tasks, file_order = build_rows(repo_root)
    if not rows:
        return []
    rows, _ = apply_default_monitor_filters(
        rows, tasks, file_order, full=False, max_visible=max_visible,
    )
    rows = relayout_tree_for_visible_rows(rows, tasks, file_order)
    return [r.to_dict() for r in rows]


class _Handler(BaseHTTPRequestHandler):
    repo_root: Path
    poll_interval: float
    max_visible: int

    def log_message(self, format, *args):
        logger.debug(format, *args)

    def finish(self):
        try:
            super().finish()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_html()
        elif self.path == "/api/rows":
            self._serve_json()
        elif self.path == "/api/stream":
            self._serve_sse()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/retry":
            self._handle_retry()
        else:
            self.send_error(404)

    def _send_json_response(self, status: int, data: dict) -> None:
        """Send a JSON response, ignoring BrokenPipeError."""
        try:
            resp = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(resp)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(resp)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _handle_retry(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
        except (BrokenPipeError, ConnectionResetError):
            return
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_json_response(400, {"ok": False, "error": "Invalid JSON"})
            return

        uuid = data.get("uuid", "").strip()
        if not uuid or not all(c in "0123456789abcdefABCDEF-" for c in uuid):
            self._send_json_response(400, {"ok": False, "error": "Invalid UUID"})
            return

        done_file = self.repo_root / "exec-done" / uuid
        if not done_file.is_file():
            self._send_json_response(404, {"ok": False, "error": "No exec-done marker found"})
            return

        try:
            done_file.unlink()
            logger.info("Retry: removed exec-done/%s", uuid)
        except OSError as e:
            self._send_json_response(500, {"ok": False, "error": str(e)})
            return

        self._send_json_response(200, {"ok": True})

    def _serve_html(self):
        body = _read_static("index.html")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self):
        data = _build_snapshot(self.repo_root, self.max_visible)
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        prev_json = ""
        try:
            while True:
                data = _build_snapshot(self.repo_root, self.max_visible)
                cur_json = json.dumps(data, ensure_ascii=False)
                if cur_json != prev_json:
                    msg = f"data: {cur_json}\n\n"
                    self.wfile.write(msg.encode("utf-8"))
                    self.wfile.flush()
                    prev_json = cur_json
                time.sleep(self.poll_interval)
        except (BrokenPipeError, ConnectionResetError):
            pass


def run_server(
    repo_root: Path,
    host: str = "127.0.0.1",
    port: int = 8080,
    poll_interval: float = 3.0,
    max_visible: int = 30,
) -> None:
    _Handler.repo_root = repo_root
    _Handler.poll_interval = poll_interval
    _Handler.max_visible = max_visible

    class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            """Suppress BrokenPipeError/ConnectionResetError from logs."""
            import sys
            exc = sys.exc_info()[1]
            if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
                logger.debug("Connection closed by client %s", client_address)
                return
            super().handle_error(request, client_address)

    server = _ThreadingHTTPServer((host, port), _Handler)
    logger.info("ghdag ui: http://%s:%d (repo: %s)", host, port, repo_root)
    print(f"ghdag ui: http://{host}:{port}  (repo: {repo_root})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
