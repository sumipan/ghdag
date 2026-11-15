"""Tests for ghdag.ui — Web UI dashboard."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _default_language_pack(monkeypatch):
    """Run every test with the bundled EN pack unless a test overrides it."""
    from ghdag.config.language import get_language_pack

    monkeypatch.delenv("GHDAG_LANGUAGE_PACK", raising=False)
    get_language_pack.cache_clear()
    yield
    get_language_pack.cache_clear()


def _write_pack(path: Path, **state_overrides: str) -> Path:
    import yaml

    from ghdag.config.language import EN

    data = {
        "state_labels": {**EN.state_labels, **state_overrides},
        "ui": dict(EN.ui),
    }
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


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
        assert row_map["aaaa-bbbb-cccc-0002"].state_id == STATE_PENDING_DEPS

    def test_build_rows_running_override(self, tmp_path):
        import json as _json

        from ghdag.ui.monitor import STATE_RUNNING, build_rows
        content = _json.dumps({"uuid": "aaaa-bbbb-cccc-dddd", "command": "echo hello", "depends": []})
        repo = self._make_repo(tmp_path, content + "\n")
        rows, _, _ = build_rows(
            repo, running_uuids_override={"aaaa-bbbb-cccc-dddd"}, detect_running=False,
        )
        assert rows[0].state_id == STATE_RUNNING


    def test_extract_engine_model(self):
        from ghdag.ui.monitor import extract_engine_model

        assert extract_engine_model("claude -p 'hello'") == "claude"
        assert extract_engine_model("claude --model claude-sonnet-4-6 -p 'hello'") == "claude/sonnet-4-6"
        assert extract_engine_model("gemini -p 'hi' -m flash") == "gemini/flash"
        assert extract_engine_model("echo hello") == ""

    def test_state_aliases_map_to_identifiers(self):
        from ghdag.config.language import STATE_IDS
        from ghdag.ui.monitor import STATE_ALIASES

        assert set(STATE_ALIASES.values()) == set(STATE_IDS)
        for state_id in STATE_IDS:
            assert STATE_ALIASES[state_id] == state_id


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


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestUiCli:

    def test_ui_missing_exec_starts_anyway(self, tmp_path):
        from ghdag.cli import main

        # UI starts even without an exec file (build_rows just returns empty)
        with patch("ghdag.ui.server.run_server") as mock_run:
            main(["ui", "--repo-root", str(tmp_path), "--port", "9999"])
            mock_run.assert_called_once()


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

    def _start(self, repo: Path):
        from http.server import HTTPServer

        from ghdag.ui.server import _Handler

        _Handler.repo_root = repo
        _Handler.poll_interval = 1.0
        _Handler.max_visible = 30
        _Handler.github_base_url = "https://github.com/o/r"
        _Handler.language_pack = None

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return server, port

    def _get_json(self, port: int, path: str):
        import urllib.request

        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5)
        return json.loads(resp.read().decode("utf-8"))

    def _make_mixed_repo(self, tmp_path: Path) -> Path:
        import json as _json

        jobs_dir = tmp_path / "jobs"
        (jobs_dir / "done").mkdir(parents=True, exist_ok=True)
        (jobs_dir / "running").mkdir(parents=True, exist_ok=True)
        (jobs_dir / "exec.jsonl").write_text(
            "".join(
                _json.dumps({"uuid": f"aaaa-bbbb-cccc-000{i}", "command": "echo", "depends": []}) + "\n"
                for i in (1, 2, 3)
            ),
            encoding="utf-8",
        )
        (jobs_dir / "done" / "aaaa-bbbb-cccc-0001").write_text("0\n", encoding="utf-8")
        (jobs_dir / "running" / "aaaa-bbbb-cccc-0002.json").write_text("{}", encoding="utf-8")
        return tmp_path


    def test_rows_state_filter(self, tmp_path):
        repo = self._make_mixed_repo(tmp_path)
        server, port = self._start(repo)
        try:
            running = self._get_json(port, "/api/rows?state=running")
            ok = self._get_json(port, "/api/rows?state=ok")
            both = self._get_json(port, "/api/rows?state=ok,running")
            unfiltered = self._get_json(port, "/api/rows")
        finally:
            server.shutdown()
        assert [r["uuid"] for r in running] == ["aaaa-bbbb-cccc-0002"]
        assert [r["uuid"] for r in ok] == ["aaaa-bbbb-cccc-0001"]
        assert {r["uuid"] for r in both} == {"aaaa-bbbb-cccc-0001", "aaaa-bbbb-cccc-0002"}
        assert len(unfiltered) == 3


    def test_config_i18n_uses_handler_pack(self, tmp_path):
        from ghdag.config.language import EN, LanguagePack
        from ghdag.ui.server import _Handler

        server, port = self._start(self._make_repo(tmp_path))
        _Handler.language_pack = LanguagePack(
            state_labels={**EN.state_labels, "ok": "Host OK"}, ui=EN.ui,
        )
        try:
            data = self._get_json(port, "/api/config")
        finally:
            server.shutdown()
            _Handler.language_pack = None
        assert data["i18n"]["state_labels"]["ok"] == "Host OK"


    def test_run_server_rejects_missing_pack_file(self, tmp_path, monkeypatch):
        from ghdag.core.exceptions import GhdagError
        from ghdag.ui import server as server_mod

        monkeypatch.setenv("GHDAG_LANGUAGE_PACK", str(tmp_path / "missing.yaml"))
        with patch.object(server_mod, "HTTPServer") as mock_http:
            with pytest.raises(GhdagError):
                server_mod.run_server(tmp_path, port=0)
            mock_http.assert_not_called()


class TestStateDir:
    """Issue sumipan/nexus#6089: ghdag ui respects GHDAG_STATE_DIR."""

    UUID = "aaaa-bbbb-cccc-0002"

    def _make_state_repo(self, tmp_path: Path) -> Path:
        import json as _json

        state = tmp_path / "s"
        jobs = tmp_path / "jobs"
        jobs.mkdir(parents=True)
        (jobs / "exec.jsonl").write_text(
            _json.dumps({"uuid": self.UUID, "command": "echo run", "depends": []}) + "\n",
            encoding="utf-8",
        )
        (state / "running").mkdir(parents=True)
        (state / "running" / f"{self.UUID}.json").write_text("{}", encoding="utf-8")
        return tmp_path

    def _start(self, repo: Path):
        from http.server import HTTPServer

        from ghdag.ui.server import _Handler

        _Handler.repo_root = repo
        _Handler.poll_interval = 1.0
        _Handler.max_visible = 30
        _Handler.github_base_url = None
        _Handler.language_pack = None

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return server, port


    def test_stop_writes_cancel_in_state_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GHDAG_STATE_DIR", str(tmp_path / "s"))
        repo = self._make_state_repo(tmp_path)
        state = tmp_path / "s"
        server, port = self._start(repo)
        try:
            import urllib.request

            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/stop",
                data=json.dumps({"uuid": self.UUID}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=5)
            body = json.loads(resp.read().decode("utf-8"))
        finally:
            server.shutdown()
        assert body["ok"] is True
        assert (state / "cancel" / self.UUID).is_file()
        assert not (tmp_path / "jobs" / "cancel").exists()
