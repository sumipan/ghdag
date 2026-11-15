"""Tests for `ghdag dag cancel` CLI."""
from __future__ import annotations

import pytest


class TestCliDagCancel:

    def test_cancel_not_running_creates_nothing(self, tmp_path, capsys, monkeypatch):
        """A non-running uuid returns not running and creates no control file."""
        from ghdag.cli import main
        jobs = tmp_path / 'jobs'
        jobs.mkdir(parents=True)
        uuid = '11111111-2222-3333-4444-555555555555'
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main(['dag', 'cancel', uuid])
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert 'not running' in captured.err.lower() or 'not running' in captured.out.lower()
        assert not (jobs / 'cancel' / uuid).exists()

    def test_api_stop_uses_cancel_file_not_ps(self, tmp_path):
        """The /api/stop path creates a control file instead of killing via ps."""
        from ghdag.ui import server as server_mod
        jobs = tmp_path / 'jobs'
        running = jobs / 'running'
        running.mkdir(parents=True)
        uuid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        (running / f'{uuid}.json').write_text('{}', encoding='utf-8')
        ok, err = server_mod._request_cancel(tmp_path, uuid)
        assert ok is True
        assert err == ''
        assert (jobs / 'cancel' / uuid).is_file()
        ok2, err2 = server_mod._request_cancel(tmp_path, '00000000-0000-0000-0000-000000000000')
        assert ok2 is False
        assert 'not running' in err2.lower() or 'No running' in err2
