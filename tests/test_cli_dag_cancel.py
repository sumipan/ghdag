"""Tests for `ghdag dag cancel` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestCliDagCancel:
    def test_cancel_creates_control_file_when_running(self, tmp_path, capsys, monkeypatch):
        """実行中 uuid への cancel は jobs/cancel/<uuid> を作る。"""
        from ghdag.cli import main

        jobs = tmp_path / "jobs"
        running = jobs / "running"
        running.mkdir(parents=True)
        uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        (running / f"{uuid}.json").write_text(
            json.dumps(
                {
                    "pid": 12345,
                    "pgid": 12345,
                    "engine": "shell",
                    "started_at": "2026-09-09T00:00:00+00:00",
                    "has_resume": False,
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        main(["dag", "cancel", uuid])

        captured = capsys.readouterr()
        cancel_path = jobs / "cancel" / uuid
        assert cancel_path.is_file()
        assert "cancel" in captured.out.lower() or uuid in captured.out

    def test_cancel_not_running_creates_nothing(self, tmp_path, capsys, monkeypatch):
        """実行中でない uuid は not running を返し制御ファイルを作らない。"""
        from ghdag.cli import main

        jobs = tmp_path / "jobs"
        jobs.mkdir(parents=True)
        uuid = "11111111-2222-3333-4444-555555555555"
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            main(["dag", "cancel", uuid])
        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "not running" in captured.err.lower() or "not running" in captured.out.lower()
        assert not (jobs / "cancel" / uuid).exists()

    def test_api_stop_uses_cancel_file_not_ps(self, tmp_path):
        """/api/stop 経路は ps 直殺しではなく制御ファイルを作成する。"""
        import inspect

        from ghdag.ui import server as server_mod

        stop_src = inspect.getsource(server_mod._Handler._handle_stop)
        assert "_kill_by_uuid" not in stop_src
        assert "ps" not in stop_src

        # _kill_by_uuid が残っていても stop から呼ばれないこと、
        # および制御ファイル作成ヘルパが running 時に cancel を作ること
        assert hasattr(server_mod, "_request_cancel")
        jobs = tmp_path / "jobs"
        running = jobs / "running"
        running.mkdir(parents=True)
        uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        (running / f"{uuid}.json").write_text("{}", encoding="utf-8")

        ok, err = server_mod._request_cancel(tmp_path, uuid)
        assert ok is True
        assert err == ""
        assert (jobs / "cancel" / uuid).is_file()

        ok2, err2 = server_mod._request_cancel(tmp_path, "00000000-0000-0000-0000-000000000000")
        assert ok2 is False
        assert "not running" in err2.lower() or "No running" in err2
