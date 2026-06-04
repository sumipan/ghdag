"""Tests for ghdag.ui — Web UI dashboard."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Monitor tests
# ---------------------------------------------------------------------------


class TestMonitor:
    def _make_repo(self, tmp_path: Path, exec_jsonl_content: str, done: dict | None = None):
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        (jobs_dir / "exec.jsonl").write_text(exec_jsonl_content, encoding="utf-8")
        done_dir = tmp_path / "jobs" / "done"
        done_dir.mkdir(parents=True, exist_ok=True)
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
        import json as _json

        from ghdag.ui.monitor import STATE_PENDING_RUN, build_rows
        content = _json.dumps({"uuid": "aaaa-bbbb-cccc-dddd", "command": "echo hello", "depends": []})
        repo = self._make_repo(tmp_path, content + "\n")
        rows, tasks, file_order = build_rows(repo, detect_running=False)
        assert len(rows) == 1
        assert rows[0].uuid == "aaaa-bbbb-cccc-dddd"
        assert rows[0].state == STATE_PENDING_RUN

    def test_build_rows_completed_task(self, tmp_path):
        import json as _json

        from ghdag.ui.monitor import STATE_OK, build_rows
        content = _json.dumps({"uuid": "aaaa-bbbb-cccc-dddd", "command": "echo hello", "depends": []})
        repo = self._make_repo(tmp_path, content + "\n", done={"aaaa-bbbb-cccc-dddd": "0"})
        rows, tasks, file_order = build_rows(repo, detect_running=False)
        assert len(rows) == 1
        assert rows[0].state == STATE_OK

    def test_build_rows_failed_task(self, tmp_path):
        import json as _json

        from ghdag.ui.monitor import STATE_FAIL, build_rows
        content = _json.dumps({"uuid": "aaaa-bbbb-cccc-dddd", "command": "echo hello", "depends": []})
        repo = self._make_repo(tmp_path, content + "\n", done={"aaaa-bbbb-cccc-dddd": "1"})
        rows, tasks, file_order = build_rows(repo, detect_running=False)
        assert rows[0].state == STATE_FAIL

    def test_build_rows_with_depends(self, tmp_path):
        import json as _json

        from ghdag.ui.monitor import STATE_PENDING_DEPS, build_rows
        content = (
            _json.dumps({"uuid": "aaaa-bbbb-cccc-0001", "command": "echo first", "depends": []}) + "\n"
            + _json.dumps({"uuid": "aaaa-bbbb-cccc-0002", "command": "echo second", "depends": ["aaaa-bbbb-cccc-0001"]}) + "\n"
        )
        repo = self._make_repo(tmp_path, content)
        rows, tasks, file_order = build_rows(repo, detect_running=False)
        assert len(rows) == 2
        row_map = {r.uuid: r for r in rows}
        assert row_map["aaaa-bbbb-cccc-0002"].state == STATE_PENDING_DEPS

    def test_build_rows_running_override(self, tmp_path):
        import json as _json

        from ghdag.ui.monitor import STATE_RUNNING, build_rows
        content = _json.dumps({"uuid": "aaaa-bbbb-cccc-dddd", "command": "echo hello", "depends": []})
        repo = self._make_repo(tmp_path, content + "\n")
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
        import json as _json

        from ghdag.ui.monitor import build_rows, filter_rows
        content = (
            _json.dumps({"uuid": "aaaa-bbbb-cccc-0001", "command": "echo first", "depends": []}) + "\n"
            + _json.dumps({"uuid": "aaaa-bbbb-cccc-0002", "command": "echo second", "depends": []}) + "\n"
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

    def test_cmd_preview_issuesmith_key(self):
        from ghdag.ui.monitor import cmd_preview

        assert cmd_preview("echo hello", idempotency_key="issuesmith:impl:1203") == "#1203 \u00b7 impl"

    def test_cmd_preview_issuesmith_merge_key(self):
        from ghdag.ui.monitor import cmd_preview

        assert cmd_preview("echo hello", idempotency_key="issuesmith:merge:42") == "#42 \u00b7 merge"

    def test_cmd_preview_empty_key_fallback(self):
        from ghdag.ui.monitor import cmd_preview

        assert cmd_preview("echo hello", idempotency_key="") == "echo hello"

    def test_cmd_preview_generic_workflow_key(self):
        from ghdag.ui.monitor import cmd_preview

        assert cmd_preview("echo hello", idempotency_key="scheduler:daily:99") == "#99 \u00b7 daily"

    def test_cmd_preview_malformed_key_fallback(self):
        from ghdag.ui.monitor import cmd_preview

        # \u30b3\u30ed\u30f3\u533a\u5207\u308a\u304c\u4e0d\u6b63\uff082\u30bb\u30af\u30b7\u30e7\u30f3\u3057\u304b\u306a\u3044\uff09
        assert cmd_preview("echo hello", idempotency_key="issuesmith:impl") == "echo hello"
        # \u672b\u5c3e\u304c\u7a7a
        assert cmd_preview("echo hello", idempotency_key="issuesmith:") == "echo hello"

    def test_cmd_preview_none_key_does_not_crash(self):
        """submit/audit/hooks \u7d4c\u8def\u306f `idempotency_key: str | None = None` \u4ed5\u69d8\u3067
        None \u3092\u51fa\u529b\u3059\u308b\u3002cmd_preview \u304c None \u3092\u53d7\u3051\u53d6\u3063\u3066\u3082 TypeError \u3067\u306f\u306a\u304f
        \u901a\u5e38\u306e\u30b3\u30de\u30f3\u30c9\u6587\u5b57\u5217\u306b\u30d5\u30a9\u30fc\u30eb\u30d0\u30c3\u30af\u3059\u308b\u3053\u3068\u3002"""
        from ghdag.ui.monitor import cmd_preview

        assert cmd_preview("echo hello", idempotency_key=None) == "echo hello"

    def test_parse_exec_jsonl_with_idempotency_key(self, tmp_path):
        import json as _json

        from ghdag.ui.monitor import _parse_exec_jsonl

        content = _json.dumps({
            "uuid": "aaaa-bbbb-cccc-dddd",
            "command": "echo hello",
            "depends": [],
            "idempotency_key": "issuesmith:impl:1203",
        })
        path = tmp_path / "exec.jsonl"
        path.write_text(content + "\n", encoding="utf-8")
        tasks, _ = _parse_exec_jsonl(str(path))
        assert tasks["aaaa-bbbb-cccc-dddd"].idempotency_key == "issuesmith:impl:1203"

    def test_parse_exec_jsonl_without_idempotency_key(self, tmp_path):
        import json as _json

        from ghdag.ui.monitor import _parse_exec_jsonl

        content = _json.dumps({
            "uuid": "aaaa-bbbb-cccc-dddd",
            "command": "echo hello",
            "depends": [],
        })
        path = tmp_path / "exec.jsonl"
        path.write_text(content + "\n", encoding="utf-8")
        tasks, _ = _parse_exec_jsonl(str(path))
        assert tasks["aaaa-bbbb-cccc-dddd"].idempotency_key == ""

    def test_parse_exec_jsonl_with_null_idempotency_key(self, tmp_path):
        """submit/audit/hooks の `idempotency_key: str | None = None` 仕様により
        exec.jsonl に `"idempotency_key": null` が書き出されるケース。
        `data.get(k, default)` の default は値が null の場合は適用されず None が
        返るため、`or ""` で空文字に正規化されないと下流の regex.match() で
        TypeError になる。"""
        import json as _json

        from ghdag.ui.monitor import _parse_exec_jsonl

        content = _json.dumps({
            "uuid": "aaaa-bbbb-cccc-dddd",
            "command": "echo hello",
            "depends": [],
            "idempotency_key": None,
        })
        path = tmp_path / "exec.jsonl"
        path.write_text(content + "\n", encoding="utf-8")
        tasks, _ = _parse_exec_jsonl(str(path))
        assert tasks["aaaa-bbbb-cccc-dddd"].idempotency_key == ""


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

    def test_ui_missing_exec_starts_anyway(self, tmp_path):
        from ghdag.cli import main

        # exec ファイルがなくても UI は起動する（build_rows が空を返すだけ）
        with patch("ghdag.ui.server.run_server") as mock_run:
            main(["ui", "--repo-root", str(tmp_path), "--port", "9999"])
            mock_run.assert_called_once()

    def test_ui_calls_run_server(self, tmp_path):
        from ghdag.cli import main

        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()
        (jobs_dir / "exec.jsonl").write_text("")

        with patch("ghdag.ui.server.run_server") as mock_run:
            main(["ui", "--repo-root", str(tmp_path), "--port", "9999"])
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args
            assert call_kwargs.kwargs["port"] == 9999


# ---------------------------------------------------------------------------
# Server tests
# ---------------------------------------------------------------------------


class TestStaticAssets:
    def test_static_dir_exists(self):
        """Static assets must be resolvable via importlib.resources (editable and wheel)."""
        from ghdag.ui.server import _STATIC_DIR

        assert _STATIC_DIR.exists(), (
            f"Static directory not found: {_STATIC_DIR}. "
            "Run 'pip install -r requirements.txt' to restore a non-editable install."
        )

    def test_index_html_exists(self):
        """index.html must be present in the installed package."""
        from ghdag.ui.server import _read_static

        html = _read_static("index.html")
        assert b"ghdag Dashboard" in html


class TestServer:
    def _make_repo(self, tmp_path: Path):
        import json as _json
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        (jobs_dir / "exec.jsonl").write_text(
            _json.dumps({"uuid": "aaaa-bbbb-cccc-0001", "command": "echo hello", "depends": []}) + "\n",
            encoding="utf-8",
        )
        (tmp_path / "jobs" / "done").mkdir(parents=True, exist_ok=True)
        return tmp_path

    def test_serve_json_endpoint(self, tmp_path):
        import urllib.request

        repo = self._make_repo(tmp_path)

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
            url = f"http://127.0.0.1:{port}/"
            resp = urllib.request.urlopen(url, timeout=5)
            html = resp.read().decode("utf-8")
            assert "ghdag Dashboard" in html
            assert "text/html" in resp.headers.get("Content-Type", "")
        finally:
            server.shutdown()
