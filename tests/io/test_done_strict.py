"""Strict done-marker semantics (Issue #5372)."""

from __future__ import annotations

import json
from pathlib import Path


class TestInterpretDoneStrict:
    def test_empty_content_is_not_success(self) -> None:
        from ghdag.io.done import interpret_done

        assert interpret_done("") != "success"
        assert interpret_done("\n") != "success"

    def test_zero_is_success(self) -> None:
        from ghdag.io.done import interpret_done

        assert interpret_done("0") == "success"
        assert interpret_done("0\n") == "success"

    def test_json_ok_is_success(self) -> None:
        from ghdag.io.done import interpret_done

        body = json.dumps({"status": "ok", "exit_code": 0, "ts": "2026-01-01T00:00:00+00:00"})
        assert interpret_done(body) == "success"

    def test_json_non_ok_is_not_success(self) -> None:
        from ghdag.io.done import interpret_done

        body = json.dumps({"status": "failed", "exit_code": 1})
        assert interpret_done(body) != "success"

    def test_invalid_json_line_is_other(self) -> None:
        from ghdag.io.done import interpret_done

        assert interpret_done("{not-json") == "other"


class TestDepSucceededStrict:
    def test_touch_empty_file_does_not_satisfy_dependency(self, tmp_path: Path) -> None:
        from ghdag.io.done import dep_succeeded

        (tmp_path / "dep").touch()
        assert dep_succeeded(tmp_path, "dep") is False

    def test_zero_file_satisfies_dependency(self, tmp_path: Path) -> None:
        from ghdag.io.done import dep_succeeded

        (tmp_path / "dep").write_text("0\n", encoding="utf-8")
        assert dep_succeeded(tmp_path, "dep") is True

    def test_json_ok_file_satisfies_dependency(self, tmp_path: Path) -> None:
        from ghdag.io.done import dep_succeeded

        body = json.dumps({"status": "ok", "exit_code": 0, "ts": "2026-01-01T00:00:00+00:00"})
        (tmp_path / "dep").write_text(body + "\n", encoding="utf-8")
        assert dep_succeeded(tmp_path, "dep") is True


class TestLoadSucceededStrict:
    def test_empty_done_file_not_in_succeeded_set(self, tmp_path: Path) -> None:
        from ghdag.io.done import load_succeeded_from_dir

        (tmp_path / "empty").touch()
        (tmp_path / "ok").write_text("0", encoding="utf-8")
        assert load_succeeded_from_dir(tmp_path) == {"ok"}

    def test_mark_done_success_writes_json(self, tmp_path: Path) -> None:
        from ghdag.io.done import interpret_done, mark_done

        mark_done(tmp_path, "task-a", 0)
        raw = (tmp_path / "task-a").read_text(encoding="utf-8")
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert data["exit_code"] == 0
        assert "ts" in data
        assert interpret_done(raw) == "success"

    def test_mark_done_nonzero_stays_plain(self, tmp_path: Path) -> None:
        from ghdag.io.done import mark_done

        mark_done(tmp_path, "task-fail", 1)
        assert (tmp_path / "task-fail").read_text(encoding="utf-8") == "1"
