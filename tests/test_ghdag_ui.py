"""Tests for ghdag.ui — Web UI dashboard."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Monitor tests
# ---------------------------------------------------------------------------


class TestMonitor:
    def _make_repo(self, tmp_path: Path, exec_md_content: str, done: dict | None = None):
        queue = tmp_path / "queue"
        queue.mkdir()
        (queue / "exec.md").write_text(exec_md_content, encoding="utf-8")
        done_dir = tmp_path / "exec-done"
        done_dir.mkdir()
        if done:
            for uuid, content in done.items():
                (done_dir / uuid).write_text(content, encoding="utf-8")
        return tmp_path

    def test_build_rows_empty(self, tmp_path):
        from ghdag.ui.monitor import build_rows

        repo = self._make_repo(tmp_path, "")
        rows, tasks, file_order = build_rows(repo, detect_running=False)
        assert rows == []
        assert tasks == {}

    def test_build_rows_single_task(self, tmp_path):
        from ghdag.ui.monitor import build_rows, STATE_PENDING_RUN

        content = "aaaa-bbbb-cccc-dddd: echo hello"
        repo = self._make_repo(tmp_path, content)
        rows, tasks, file_order = build_rows(repo, detect_running=False)
        assert len(rows) == 1
        assert rows[0].uuid == "aaaa-bbbb-cccc-dddd"
        assert rows[0].state == STATE_PENDING_RUN

    def test_build_rows_completed_task(self, tmp_path):
        from ghdag.ui.monitor import build_rows, STATE_OK

        content = "aaaa-bbbb-cccc-dddd: echo hello"
        repo = self._make_repo(tmp_path, content, done={"aaaa-bbbb-cccc-dddd": "0"})
        rows, tasks, file_order = build_rows(repo, detect_running=False)
        assert len(rows) == 1
        assert rows[0].state == STATE_OK

    def test_build_rows_failed_task(self, tmp_path):
        from ghdag.ui.monitor import build_rows, STATE_FAIL

        content = "aaaa-bbbb-cccc-dddd: echo hello"
        repo = self._make_repo(tmp_path, content, done={"aaaa-bbbb-cccc-dddd": "1"})
        rows, tasks, file_order = build_rows(repo, detect_running=False)
        assert rows[0].state == STATE_FAIL

    def test_build_rows_with_depends(self, tmp_path):
        from ghdag.ui.monitor import build_rows, STATE_PENDING_DEPS

        content = (
            "aaaa-bbbb-cccc-0001: echo first\n"
            "aaaa-bbbb-cccc-0002[depends:aaaa-bbbb-cccc-0001]: echo second\n"
        )
        repo = self._make_repo(tmp_path, content)
        rows, tasks, file_order = build_rows(repo, detect_running=False)
        assert len(rows) == 2
        row_map = {r.uuid: r for r in rows}
        assert row_map["aaaa-bbbb-cccc-0002"].state == STATE_PENDING_DEPS

    def test_build_rows_running_override(self, tmp_path):
        from ghdag.ui.monitor import build_rows, STATE_RUNNING

        content = "aaaa-bbbb-cccc-dddd: echo hello"
        repo = self._make_repo(tmp_path, content)
        rows, _, _ = build_rows(
            repo, running_uuids_override={"aaaa-bbbb-cccc-dddd"}, detect_running=False,
        )
        assert rows[0].state == STATE_RUNNING

    def test_extract_engine_model(self):
        from ghdag.ui.monitor import extract_engine_model

        assert extract_engine_model("claude -p 'hello'") == "claude"
        assert extract_engine_model("claude --model claude-sonnet-4-6 -p 'hello'") == "claude/sonnet-4-6"
        assert extract_engine_model("gemini -p 'hi' -m flash") == "gemini/flash"
        assert extract_engine_model("echo hello") == ""

    def test_filter_rows_by_state(self, tmp_path):
        from ghdag.ui.monitor import build_rows, filter_rows

        content = (
            "aaaa-bbbb-cccc-0001: echo first\n"
            "aaaa-bbbb-cccc-0002: echo second\n"
        )
        repo = self._make_repo(tmp_path, content, done={"aaaa-bbbb-cccc-0001": "0"})
        rows, _, _ = build_rows(repo, detect_running=False)
        filtered = filter_rows(rows, None, {"ok"})
        assert len(filtered) == 1
        assert filtered[0].uuid == "aaaa-bbbb-cccc-0001"

    def test_row_to_dict(self):
        from ghdag.ui.monitor import Row

        r = Row(uuid="abc", state="running", cmd_preview="echo", tree_ts="2026", engine_model="claude")
        d = r.to_dict()
        assert d["uuid"] == "abc"
        assert d["state"] == "running"

    def test_queue_ts_parsing(self):
        from ghdag.ui.monitor import ts_display

        assert ts_display("cat queue/20260413223000-order.md") == "2026-04-13 22:30"
        assert ts_display("echo hello") == "\u2014"


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestUiCli:
    def test_ui_help_exits_0(self, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["ui", "--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "--host" in captured.out
        assert "--port" in captured.out
        assert "--repo-root" in captured.out

    def test_ui_missing_exec_md_exits_1(self, tmp_path, capsys):
        from ghdag.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["ui", "--repo-root", str(tmp_path)])
        assert exc.value.code == 1
        assert "exec.md not found" in capsys.readouterr().err

    def test_ui_calls_run_server(self, tmp_path):
        from ghdag.cli import main

        queue = tmp_path / "queue"
        queue.mkdir()
        (queue / "exec.md").write_text("")

        with patch("ghdag.ui.server.run_server") as mock_run:
            main(["ui", "--repo-root", str(tmp_path), "--port", "9999"])
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args
            assert call_kwargs.kwargs["port"] == 9999


# ---------------------------------------------------------------------------
# Server tests
# ---------------------------------------------------------------------------


class TestServer:
    def _make_repo(self, tmp_path: Path):
        queue = tmp_path / "queue"
        queue.mkdir()
        (queue / "exec.md").write_text(
            "aaaa-bbbb-cccc-0001: echo hello\n",
            encoding="utf-8",
        )
        (tmp_path / "exec-done").mkdir()
        return tmp_path

    def test_serve_json_endpoint(self, tmp_path):
        import urllib.request

        repo = self._make_repo(tmp_path)

        from ghdag.ui.server import run_server
        from http.server import HTTPServer
        from ghdag.ui.server import _Handler

        _Handler.repo_root = repo
        _Handler.poll_interval = 1.0
        _Handler.max_visible = 30

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        try:
            url = f"http://127.0.0.1:{port}/api/rows"
            resp = urllib.request.urlopen(url, timeout=5)
            data = json.loads(resp.read().decode("utf-8"))
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["uuid"] == "aaaa-bbbb-cccc-0001"
        finally:
            server.shutdown()

    def test_serve_html_endpoint(self, tmp_path):
        import urllib.request

        repo = self._make_repo(tmp_path)

        from ghdag.ui.server import _Handler
        from http.server import HTTPServer

        _Handler.repo_root = repo
        _Handler.poll_interval = 1.0
        _Handler.max_visible = 30

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        try:
            url = f"http://127.0.0.1:{port}/"
            resp = urllib.request.urlopen(url, timeout=5)
            html = resp.read().decode("utf-8")
            assert "ghdag Dashboard" in html
            assert "text/html" in resp.headers.get("Content-Type", "")
        finally:
            server.shutdown()
