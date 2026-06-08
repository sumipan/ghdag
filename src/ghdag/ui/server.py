"""Lightweight HTTP server with SSE for the ghdag Web UI dashboard."""

from __future__ import annotations

import importlib.resources
import json
import logging
import os
import re
import signal
import subprocess
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

from ghdag.pipeline.audit_query import get_correlation_top_n

from .monitor import (
    apply_default_monitor_filters,
    build_rows,
    relayout_tree_for_visible_rows,
)

logger = logging.getLogger(__name__)

# Resolve static dir via importlib.resources so it works for both editable and
# wheel installs regardless of __file__ location.
_STATIC_DIR: Path = Path(
    importlib.resources.files("ghdag.ui").joinpath("static")  # type: ignore[arg-type]
)


def _get_github_base_url(repo_root: Path) -> str | None:
    """Read GitHub repo base URL from git remote origin. Returns None if unavailable."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode != 0:
            return None
        url = result.stdout.strip()
        m = re.match(r"git@github\.com:([^/]+/[^/]+?)(?:\.git)?$", url)
        if m:
            return "https://github.com/" + m.group(1)
        m = re.match(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?$", url)
        if m:
            return "https://github.com/" + m.group(1)
        return None
    except Exception:
        return None


def _read_static(filename: str) -> bytes:
    path = _STATIC_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Static asset missing: {path}. "
            "Reinstall ghdag from PyPI/git URL (not editable) to get bundled assets."
        )
    return path.read_bytes()


def _build_snapshot(repo_root: Path, max_visible: int = 30) -> list[dict]:
    rows, tasks, file_order = build_rows(repo_root)
    if not rows:
        return []
    rows, _ = apply_default_monitor_filters(
        rows, tasks, file_order, full=False, max_visible=max_visible,
    )
    rows = relayout_tree_for_visible_rows(rows, tasks, file_order)
    return [r.to_dict() for r in rows]


def _kill_by_uuid(uuid: str) -> tuple[bool, str]:
    """Find and SIGTERM all processes whose command line contains the UUID."""
    try:
        result = subprocess.run(
            ["ps", "auxww"], capture_output=True, text=True, timeout=15, check=False
        )
        if result.returncode != 0:
            return False, "ps command failed"
        pids = []
        for line in result.stdout.splitlines():
            if uuid.lower() in line.lower():
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        pids.append(int(parts[1]))
                    except ValueError:
                        pass
        if not pids:
            return False, "No running process found for that UUID"
        killed = []
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
                killed.append(pid)
                logger.info("Stop: sent SIGTERM to pid %d (uuid=%s)", pid, uuid)
            except (ProcessLookupError, PermissionError) as e:
                logger.warning("Stop: could not kill pid %d: %s", pid, e)
        if killed:
            return True, ""
        return False, "Could not send SIGTERM to any process"
    except Exception as e:
        return False, str(e)


class _Handler(BaseHTTPRequestHandler):
    repo_root: Path
    poll_interval: float
    max_visible: int
    github_base_url: str | None

    def log_message(self, format, *args):
        logger.debug(format, *args)

    def finish(self):
        try:
            super().finish()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _parse_max_visible(self) -> int:
        qs = parse_qs(urlparse(self.path).query)
        vals = qs.get("max_visible", [])
        if vals:
            try:
                v = int(vals[0])
                if v > 0:
                    return v
            except ValueError:
                pass
        return self.max_visible

    def do_GET(self):
        parsed_path = urlparse(self.path).path
        if parsed_path == "/" or parsed_path == "/index.html":
            self._serve_html()
        elif parsed_path == "/api/rows":
            self._serve_json()
        elif parsed_path == "/api/stream":
            self._serve_sse()
        elif parsed_path == "/api/config":
            self._serve_config()
        elif parsed_path == "/api/correlation-bursts":
            self._serve_correlation_bursts()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/retry":
            self._handle_retry()
        elif self.path == "/api/stop":
            self._handle_stop()
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

    def _read_body(self) -> bytes | None:
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            return self.rfile.read(content_length)
        except (BrokenPipeError, ConnectionResetError):
            return None

    def _parse_uuid_body(self) -> str | None:
        body = self._read_body()
        if body is None:
            return None
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_json_response(400, {"ok": False, "error": "Invalid JSON"})
            return None
        uuid = data.get("uuid", "").strip()
        if not uuid or not all(c in "0123456789abcdefABCDEF-" for c in uuid):
            self._send_json_response(400, {"ok": False, "error": "Invalid UUID"})
            return None
        return uuid

    def _handle_retry(self):
        uuid = self._parse_uuid_body()
        if uuid is None:
            return

        done_file = self.repo_root / "jobs" / "done" / uuid
        if not done_file.is_file():
            self._send_json_response(404, {"ok": False, "error": "No done marker found"})
            return

        try:
            done_file.unlink()
            logger.info("Retry: removed jobs/done/%s", uuid)
        except OSError as e:
            self._send_json_response(500, {"ok": False, "error": str(e)})
            return

        self._send_json_response(200, {"ok": True})

    def _handle_stop(self):
        uuid = self._parse_uuid_body()
        if uuid is None:
            return

        ok, err = _kill_by_uuid(uuid)
        if ok:
            self._send_json_response(200, {"ok": True})
        else:
            self._send_json_response(404, {"ok": False, "error": err})

    def _serve_config(self):
        data = {"github_base_url": self.github_base_url}
        self._send_json_response(200, data)

    def _serve_correlation_bursts(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        hours = float(qs.get("hours", ["1"])[0])
        top_n = int(qs.get("top_n", ["20"])[0])
        audit_path = Path(self.repo_root) / "jobs" / "audit.jsonl"
        bursts = get_correlation_top_n(audit_path, since_sec=hours * 3600, top_n=top_n)
        self._send_json_response(200, {"bursts": bursts})

    def _serve_html(self):
        body = _read_static("index.html")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_json(self):
        data = _build_snapshot(self.repo_root, self._parse_max_visible())
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

        max_visible = self._parse_max_visible()
        prev_json = ""
        try:
            while True:
                data = _build_snapshot(self.repo_root, max_visible)
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
    _Handler.github_base_url = _get_github_base_url(repo_root)

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
