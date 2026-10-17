"""CSRF / Origin checks and CORS headers on the ghdag UI server."""

from __future__ import annotations

import json
import threading
from http.server import HTTPServer
from pathlib import Path

from ghdag.ui.server import _Handler


def _start_server(repo_root: Path) -> tuple[HTTPServer, int]:
    _Handler.repo_root = repo_root
    _Handler.poll_interval = 1.0
    _Handler.max_visible = 30
    _Handler.github_base_url = None
    _Handler.language_pack = None

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def _post(
    port: int,
    path: str,
    body: dict,
    *,
    content_type: str = "application/json",
    origin: str | None = None,
    sec_fetch_site: str | None = None,
) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": content_type}
    if origin is not None:
        headers["Origin"] = origin
    if sec_fetch_site is not None:
        headers["Sec-Fetch-Site"] = sec_fetch_site
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers=headers,
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        status = resp.status
        payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status = exc.code
        payload = json.loads(exc.read().decode("utf-8"))
    return status, payload


def _setup_running_repo(tmp_path: Path, uuid: str) -> Path:
    jobs = tmp_path / "jobs"
    (jobs / "running").mkdir(parents=True)
    (jobs / f"running/{uuid}.json").write_text("{}", encoding="utf-8")
    return tmp_path


class TestServerCsrf:
    UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_stop_rejects_evil_origin_and_writes_no_cancel(self, tmp_path):
        repo = _setup_running_repo(tmp_path, self.UUID)
        server, port = _start_server(repo)
        try:
            status, data = _post(
                port,
                "/api/stop",
                {"uuid": self.UUID},
                origin="http://evil.example",
            )
        finally:
            server.shutdown()

        assert status == 403
        assert data.get("ok") is False
        assert not (repo / "jobs" / "cancel" / self.UUID).exists()

    def test_stop_rejects_cross_site_without_origin(self, tmp_path):
        repo = _setup_running_repo(tmp_path, self.UUID)
        server, port = _start_server(repo)
        try:
            status, _data = _post(
                port,
                "/api/stop",
                {"uuid": self.UUID},
                sec_fetch_site="cross-site",
            )
        finally:
            server.shutdown()

        assert status == 403
        assert not (repo / "jobs" / "cancel" / self.UUID).exists()

    def test_mutating_post_rejects_non_json_content_type(self, tmp_path):
        repo = _setup_running_repo(tmp_path, self.UUID)
        server, port = _start_server(repo)
        try:
            status, data = _post(
                port,
                "/api/stop",
                {"uuid": self.UUID},
                content_type="text/plain",
                origin=f"http://127.0.0.1:{port}",
            )
        finally:
            server.shutdown()

        assert status == 415
        assert data.get("ok") is False

    def test_stop_same_origin_json_succeeds(self, tmp_path):
        repo = _setup_running_repo(tmp_path, self.UUID)
        server, port = _start_server(repo)
        try:
            status, data = _post(
                port,
                "/api/stop",
                {"uuid": self.UUID},
                origin=f"http://127.0.0.1:{port}",
            )
        finally:
            server.shutdown()

        assert status == 200
        assert data == {"ok": True}
        assert (repo / "jobs" / "cancel" / self.UUID).is_file()

    def test_retry_same_origin_json_succeeds(self, tmp_path):
        jobs = tmp_path / "jobs"
        (jobs / "done").mkdir(parents=True)
        done_uuid = "11111111-2222-3333-4444-555555555555"
        (jobs / "done" / done_uuid).write_text("0\n", encoding="utf-8")

        server, port = _start_server(tmp_path)
        try:
            status, data = _post(
                port,
                "/api/retry",
                {"uuid": done_uuid},
                origin=f"http://127.0.0.1:{port}",
            )
        finally:
            server.shutdown()

        assert status == 200
        assert data == {"ok": True}
        assert not (jobs / "done" / done_uuid).exists()

    def test_rows_response_has_no_access_control_allow_origin(self, tmp_path):
        jobs = tmp_path / "jobs"
        jobs.mkdir()
        (jobs / "exec.jsonl").write_text("", encoding="utf-8")

        server, port = _start_server(tmp_path)
        try:
            import urllib.request

            resp = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/rows",
                timeout=5,
            )
            assert "Access-Control-Allow-Origin" not in resp.headers
        finally:
            server.shutdown()
